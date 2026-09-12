import fs from "node:fs/promises";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const { FileBlob, SpreadsheetFile, Workbook } = require("@oai/artifact-tool");

async function build(payloadPath, outputPath, previewDir) {
  const payload = JSON.parse(await fs.readFile(payloadPath, "utf8"));
  const workbook = Workbook.create();
  const review = workbook.worksheets.add("Review");
  const instructions = workbook.worksheets.add("Instructions");
  const rowCount = payload.rows.length + 1;

  review.showGridLines = false;
  review.freezePanes.freezeRows(1);
  review.getRange(`A1:M${rowCount}`).values = [payload.headers, ...payload.rows];
  review.getRange(`A1:M${rowCount}`).format.font = { name: "Arial", size: 10, color: "#17212B" };
  review.getRange("A1:M1").format = {
    fill: "#18242E",
    font: { name: "Arial", size: 10, bold: true, color: "#FFFFFF" },
    horizontalAlignment: "center",
    verticalAlignment: "center",
    wrapText: true,
    rowHeight: 34,
  };
  review.getRange(`A2:I${rowCount}`).format.fill = "#F2F4F5";
  review.getRange(`J2:M${rowCount}`).format.fill = "#FFF8D8";
  review.getRange(`D2:M${rowCount}`).format.wrapText = true;
  review.getRange(`A2:M${rowCount}`).format.verticalAlignment = "top";
  review.getRange(`A2:M${rowCount}`).format.rowHeight = 88;
  review.getRange(`A2:C${rowCount}`).format.horizontalAlignment = "center";
  review.getRange(`J2:J${rowCount}`).dataValidation = {
    rule: { type: "list", values: payload.grounding_values },
  };
  review.getRange(`K2:K${rowCount}`).dataValidation = {
    rule: { type: "list", values: payload.usefulness_values },
  };
  review.getRange(`L2:L${rowCount}`).dataValidation = {
    rule: { type: "list", values: payload.misleading_values },
  };
  const widths = [70, 185, 95, 250, 260, 520, 380, 120, 290, 125, 115, 95, 260];
  widths.forEach((width, index) => {
    review.getRangeByIndexes(0, index, rowCount, 1).format.columnWidthPx = width;
  });
  const table = review.tables.add(`A1:M${rowCount}`, true, "Phase13CHumanReview");
  table.style = "TableStyleLight9";
  table.showFilterButton = true;

  instructions.showGridLines = false;
  instructions.getRange("A1:B10").values = [
    ["Phase 13C human review", null],
    ["Review the generated answer only against the supplied facts and evidence.", null],
    [null, null],
    [1, "Choose a Grounding Verdict, Usefulness, and Misleading value for every row."],
    [2, "Do not edit the question, evidence, answer, citations, or deterministic summary."],
    [3, "Use Notes for concise review observations."],
    [null, null],
    ["Required review rows", payload.rows.length],
    ["Benchmark SHA-256", payload.benchmark_sha256],
    ["Review schema version", payload.schema_version],
  ];
  instructions.getRange("A1:B10").format.font = { name: "Arial", size: 11, color: "#17212B" };
  instructions.getRange("A1:B1").format.font = { name: "Arial", size: 16, bold: true, color: "#17212B" };
  instructions.getRange("A2:B2").format.font = { name: "Arial", size: 10, italic: true, color: "#5D6B75" };
  instructions.getRange("A4:A6").format = {
    fill: "#E8EEF2",
    font: { name: "Arial", size: 10, bold: true, color: "#17212B" },
    horizontalAlignment: "center",
  };
  instructions.getRange("B4:B6").format.wrapText = true;
  instructions.getRange("A8:A10").format = {
    fill: "#18242E",
    font: { name: "Arial", size: 10, bold: true, color: "#FFFFFF" },
  };
  instructions.getRange("B8:B10").format.fill = "#F2F4F5";
  instructions.getRange("A1:A10").format.columnWidthPx = 210;
  instructions.getRange("B1:B10").format.columnWidthPx = 700;

  const reviewCheck = await workbook.inspect({
    kind: "table",
    sheetId: "Review",
    range: "A1:M6",
    include: "values,formulas",
    tableMaxRows: 6,
    tableMaxCols: 13,
    maxChars: 5000,
  });
  const errors = await workbook.inspect({
    kind: "match",
    searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A|#NUM!|#NULL!|#SPILL!|#CALC!",
    options: { useRegex: true, maxResults: 100 },
    summary: "final formula error scan",
  });
  console.log(reviewCheck.ndjson);
  console.log(errors.ndjson);

  await fs.mkdir(previewDir, { recursive: true });
  for (const sheetName of ["Review", "Instructions"]) {
    const preview = await workbook.render({ sheetName, autoCrop: "all", scale: 1, format: "png" });
    await fs.writeFile(
      `${previewDir}/${sheetName.toLowerCase()}.png`,
      new Uint8Array(await preview.arrayBuffer()),
    );
  }
  const output = await SpreadsheetFile.exportXlsx(workbook);
  await output.save(outputPath);
}

async function inspect(inputPath, outputPath) {
  const workbook = await SpreadsheetFile.importXlsx(await FileBlob.load(inputPath));
  const review = workbook.worksheets.getItem("Review");
  const instructions = workbook.worksheets.getItem("Instructions");
  const result = {
    review_values: review.getRange("A1:M100").values,
    instructions_values: instructions.getRange("A1:B10").values,
  };
  await fs.writeFile(outputPath, JSON.stringify(result));
}

const [command, first, second, third] = process.argv.slice(2);
if (command === "build") {
  await build(first, second, third);
} else if (command === "inspect") {
  await inspect(first, second);
} else {
  throw new Error(`unknown command: ${command}`);
}
