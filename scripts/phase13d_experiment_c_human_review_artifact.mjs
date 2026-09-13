import fs from "node:fs/promises";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const { FileBlob, SpreadsheetFile, Workbook } = require("@oai/artifact-tool");

async function build(payloadPath, outputPath, previewDir) {
  const payload = JSON.parse(await fs.readFile(payloadPath, "utf8"));
  const workbook = Workbook.create();
  const review = workbook.worksheets.add("Human Review");
  const instructions = workbook.worksheets.add("Instructions");
  const rowCount = payload.rows.length + 1;

  review.showGridLines = false;
  review.freezePanes.freezeRows(1);
  review.freezePanes.freezeColumns(3);
  review.getRange(`A1:N${rowCount}`).values = [payload.headers, ...payload.rows];
  review.getRange(`A1:N${rowCount}`).format.font = { name: "Arial", size: 10, color: "#17212B" };
  review.getRange("A1:N1").format = { fill: "#18242E", font: { name: "Arial", size: 10, bold: true, color: "#FFFFFF" }, horizontalAlignment: "center", verticalAlignment: "center", wrapText: true, rowHeight: 36 };
  review.getRange(`A2:J${rowCount}`).format.fill = "#F2F4F5";
  review.getRange(`K2:N${rowCount}`).format.fill = "#FFF4CC";
  review.getRange(`D2:N${rowCount}`).format.wrapText = true;
  review.getRange(`A2:N${rowCount}`).format.verticalAlignment = "top";
  review.getRange(`A2:N${rowCount}`).format.rowHeight = 110;
  review.getRange(`A2:C${rowCount}`).format.horizontalAlignment = "center";
  review.getRange(`K2:K${rowCount}`).dataValidation = { rule: { type: "list", values: payload.grounding_values } };
  review.getRange(`L2:L${rowCount}`).dataValidation = { rule: { type: "list", values: payload.usefulness_values } };
  review.getRange(`M2:M${rowCount}`).dataValidation = { rule: { type: "list", values: payload.misleading_values } };
  const widths = [75, 205, 95, 300, 360, 560, 420, 130, 145, 130, 110, 115, 95, 300];
  widths.forEach((width, index) => { review.getRangeByIndexes(0, index, rowCount, 1).format.columnWidthPx = width; });
  const table = review.tables.add(`A1:N${rowCount}`, true, "Phase13DExperimentCHumanReview");
  table.style = "TableStyleLight9";
  table.showFilterButton = true;

  instructions.showGridLines = false;
  instructions.getRange("A1:B16").values = [
    ["Phase 13D Experiment C human review", null],
    ["Review every successfully parsed 1280-token fallback answer against only its supplied facts and evidence.", null],
    [null, null],
    [1, "Complete Grounding, Usefulness, and Misleading for every row."],
    [2, "Do not edit columns A:J. Validation protects the frozen review context."],
    [3, "Judge each fallback answer independently; do not infer quality from automated metrics."],
    [4, "Use notes for unsupported claims, citation mismatch, exact-fact corruption, misleading omissions, refusal errors, malformed content, or evidence defects."],
    [null, null],
    ["Grounding", "PASS: supported; MINOR: limited non-material issue; FAIL: unsupported, corrupted, or materially incomplete."],
    ["Usefulness", "GOOD: directly useful; ACCEPTABLE: usable with limitations; POOR: not useful."],
    ["Misleading", "YES if a reader could form a materially wrong belief; otherwise NO."],
    [null, null],
    ["Required review rows", payload.review_rows],
    ["Review population", "All successfully parsed Experiment C 1280-token fallback answers; no sampling."],
    ["Human verdict status", "Blank — genuine reviewer input required"],
    ["Methodological status", "Development/diagnostic benchmark; not an untouched production-readiness holdout."],
  ];
  instructions.getRange("A1:B16").format.font = { name: "Arial", size: 11, color: "#17212B" };
  instructions.getRange("A1:B1").format.font = { name: "Arial", size: 16, bold: true, color: "#17212B" };
  instructions.getRange("A2:B2").format.font = { name: "Arial", size: 10, italic: true, color: "#5D6B75" };
  instructions.getRange("A4:A7").format = { fill: "#E8EEF2", font: { name: "Arial", size: 10, bold: true, color: "#17212B" }, horizontalAlignment: "center" };
  instructions.getRange("A9:A11").format = { fill: "#DCE8F0", font: { name: "Arial", size: 10, bold: true, color: "#17212B" } };
  instructions.getRange("A13:A16").format = { fill: "#18242E", font: { name: "Arial", size: 10, bold: true, color: "#FFFFFF" } };
  instructions.getRange("B4:B16").format.wrapText = true;
  instructions.getRange("B13:B16").format.fill = "#F2F4F5";
  instructions.getRange("A1:A16").format.columnWidthPx = 205;
  instructions.getRange("B1:B16").format.columnWidthPx = 760;
  instructions.getRange("A1:B16").format.autofitRows();

  const tableCheck = await workbook.inspect({ kind: "table", sheetId: "Human Review", range: `A1:N${rowCount}`, include: "values,formulas", tableMaxRows: rowCount, tableMaxCols: 14, maxChars: 12000 });
  const errors = await workbook.inspect({ kind: "match", searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A|#NUM!|#NULL!|#SPILL!|#CALC!", options: { useRegex: true, maxResults: 100 }, summary: "final formula error scan" });
  console.log(tableCheck.ndjson);
  console.log(errors.ndjson);

  await fs.mkdir(previewDir, { recursive: true });
  for (const sheetName of ["Human Review", "Instructions"]) {
    const range = sheetName === "Human Review" ? `A1:N${rowCount}` : "A1:B16";
    const preview = await workbook.render({ sheetName, range, scale: 1, format: "png" });
    await fs.writeFile(`${previewDir}/${sheetName.toLowerCase().replace(" ", "_")}.png`, new Uint8Array(await preview.arrayBuffer()));
  }
  const output = await SpreadsheetFile.exportXlsx(workbook);
  await output.save(outputPath);
}

async function inspect(inputPath, outputPath) {
  const workbook = await SpreadsheetFile.importXlsx(await FileBlob.load(inputPath));
  const result = {
    review_values: workbook.worksheets.getItem("Human Review").getRange("A1:N200").values,
    instructions_values: workbook.worksheets.getItem("Instructions").getRange("A1:B20").values,
  };
  await fs.writeFile(outputPath, JSON.stringify(result));
}

const [command, first, second, third] = process.argv.slice(2);
if (command === "build") await build(first, second, third);
else if (command === "inspect") await inspect(first, second);
else throw new Error(`unknown command: ${command}`);
