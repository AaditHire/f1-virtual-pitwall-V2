import fs from "node:fs/promises";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const { FileBlob, SpreadsheetFile, Workbook } = require("@oai/artifact-tool");

async function build(payloadPath, outputPath, previewDir) {
  const payload = JSON.parse(await fs.readFile(payloadPath, "utf8"));
  const workbook = Workbook.create();
  const annotations = workbook.worksheets.add("Annotations");
  const instructions = workbook.worksheets.add("Instructions");

  annotations.showGridLines = false;
  annotations.freezePanes.freezeRows(1);
  annotations.getRange("A1:O31").values = [payload.headers, ...payload.rows];
  annotations.getRange("J2:J31").formulas = payload.audio_formulas.map((formula) => [formula]);
  annotations.getRange("A1:O31").format.font = { name: "Arial", size: 10, color: "#17212B" };
  annotations.getRange("A1:O1").format = {
    fill: "#18242E",
    font: { name: "Arial", size: 10, bold: true, color: "#FFFFFF" },
    horizontalAlignment: "center",
    verticalAlignment: "center",
    wrapText: true,
    rowHeight: 34,
  };
  annotations.getRange("A2:I31").format.fill = "#F2F4F5";
  annotations.getRange("A2:I31").format.font = { name: "Arial", size: 10, color: "#43515C" };
  annotations.getRange("J2:J31").format.fill = "#E8F1FB";
  annotations.getRange("J2:J31").format.font = { name: "Arial", size: 10, color: "#1557A0" };
  annotations.getRange("K2:O31").format.fill = "#FFF8D8";
  annotations.getRange("K2:O31").format.verticalAlignment = "top";
  annotations.getRange("K2:K31").format.wrapText = true;
  annotations.getRange("N2:O31").format.wrapText = true;
  annotations.getRange("A2:O31").format.rowHeight = 54;
  annotations.getRange("A2:A31").format.horizontalAlignment = "center";
  annotations.getRange("C2:C31").format.horizontalAlignment = "center";
  annotations.getRange("H2:H31").format.horizontalAlignment = "center";
  annotations.getRange("J2:J31").format.horizontalAlignment = "center";
  annotations.getRange("A2:A31").format.numberFormat = "0";
  annotations.getRange("C2:C31").format.numberFormat = "0";
  annotations.getRange("H2:H31").format.numberFormat = "0";
  annotations.getRange("B2:B31").format.numberFormat = "@";
  annotations.getRange("G2:G31").format.numberFormat = "@";
  annotations.getRange("I2:I31").format.numberFormat = "@";
  const widths = [78, 180, 68, 150, 78, 150, 122, 78, 145, 94, 360, 100, 105, 190, 260];
  widths.forEach((width, index) => {
    annotations.getRangeByIndexes(0, index, 31, 1).format.columnWidthPx = width;
  });
  annotations.getRange("L2:L31").dataValidation = {
    rule: { type: "list", values: payload.usability_values },
  };
  annotations.getRange("M2:M31").dataValidation = {
    rule: { type: "list", values: payload.speaker_values },
  };
  const table = annotations.tables.add("A1:O31", true, "Phase12CAnnotations");
  table.style = "TableStyleLight9";
  table.showFilterButton = true;

  instructions.showGridLines = false;
  instructions.getRange("A1:B16").values = [
    ["Phase 12C manual transcript annotation", null],
    ["Enter only what you hear in the frozen audio clips.", null],
    [null, null],
    [1, "Click Open audio in the Annotations sheet."],
    [2, "Listen without viewing any machine transcript or model output."],
    [3, "Type exactly what you hear in Human Transcript."],
    [4, "Do not rewrite speech into cleaner English."],
    [5, "Use [inaudible] instead of guessing."],
    [6, "Select usability and speaker type."],
    [7, "Critical Terms and Notes are optional."],
    [8, "Save this workbook when all 30 rows are finished."],
    [9, "Return the completed workbook for protected Phase 12C import."],
    [null, null],
    ["Frozen dataset size", 30],
    ["Frozen selection SHA-256", payload.selection_hash],
    ["Annotation schema version", payload.schema_version],
  ];
  instructions.getRange("A1:B16").format.font = { name: "Arial", size: 11, color: "#17212B" };
  instructions.getRange("A1:B1").format.font = {
    name: "Arial",
    size: 16,
    bold: true,
    color: "#17212B",
  };
  instructions.getRange("A2:B2").format.font = {
    name: "Arial",
    size: 10,
    italic: true,
    color: "#5D6B75",
  };
  instructions.getRange("A4:A12").format = {
    fill: "#E8EEF2",
    font: { name: "Arial", size: 10, bold: true, color: "#17212B" },
    horizontalAlignment: "center",
  };
  instructions.getRange("B4:B12").format.wrapText = true;
  instructions.getRange("A14:A16").format = {
    fill: "#18242E",
    font: { name: "Arial", size: 10, bold: true, color: "#FFFFFF" },
  };
  instructions.getRange("B14:B16").format.fill = "#F2F4F5";
  instructions.getRange("A1:A16").format.columnWidthPx = 210;
  instructions.getRange("B1:B16").format.columnWidthPx = 650;
  instructions.getRange("A4:B12").format.rowHeight = 30;
  instructions.getRange("A14:B16").format.rowHeight = 26;

  const annotationCheck = await workbook.inspect({
    kind: "table",
    sheetId: "Annotations",
    range: "A1:O6",
    include: "values,formulas",
    tableMaxRows: 6,
    tableMaxCols: 15,
    maxChars: 4500,
  });
  const instructionCheck = await workbook.inspect({
    kind: "table",
    sheetId: "Instructions",
    range: "A1:B16",
    include: "values,formulas",
    tableMaxRows: 16,
    tableMaxCols: 2,
    maxChars: 3500,
  });
  const errors = await workbook.inspect({
    kind: "match",
    searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A|#NUM!|#NULL!|#SPILL!|#CALC!",
    options: { useRegex: true, maxResults: 100 },
    summary: "final formula error scan",
  });
  console.log(annotationCheck.ndjson);
  console.log(instructionCheck.ndjson);
  console.log(errors.ndjson);

  await fs.mkdir(previewDir, { recursive: true });
  for (const sheetName of ["Annotations", "Instructions"]) {
    const preview = await workbook.render({ sheetName, autoCrop: "all", scale: 1, format: "png" });
    await fs.writeFile(
      `${previewDir}/${sheetName.toLowerCase()}.png`,
      new Uint8Array(await preview.arrayBuffer()),
    );
  }
  const output = await SpreadsheetFile.exportXlsx(workbook);
  await output.save(outputPath);
}

async function render(inputPath, previewDir) {
  const workbook = await SpreadsheetFile.importXlsx(await FileBlob.load(inputPath));
  await fs.mkdir(previewDir, { recursive: true });
  for (const sheetName of ["Annotations", "Instructions"]) {
    const preview = await workbook.render({ sheetName, autoCrop: "all", scale: 1, format: "png" });
    await fs.writeFile(
      `${previewDir}/${sheetName.toLowerCase()}.png`,
      new Uint8Array(await preview.arrayBuffer()),
    );
  }
}

async function inspect(inputPath, outputPath) {
  const workbook = await SpreadsheetFile.importXlsx(await FileBlob.load(inputPath));
  const annotations = workbook.worksheets.getItem("Annotations");
  const instructions = workbook.worksheets.getItem("Instructions");
  const result = {
    annotations_values: annotations.getRange("A1:O32").values,
    annotations_formulas: annotations.getRange("A1:O32").formulas,
    instructions_values: instructions.getRange("A1:B16").values,
  };
  await fs.writeFile(outputPath, JSON.stringify(result));
}

const [command, first, second, third] = process.argv.slice(2);
if (command === "build") {
  await build(first, second, third);
} else if (command === "inspect") {
  await inspect(first, second);
} else if (command === "render") {
  await render(first, second);
} else {
  throw new Error(`unknown command: ${command}`);
}
