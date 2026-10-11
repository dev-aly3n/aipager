// Runs the Mini App's own clock (the lines between its "// clock: port of
// aipager/clockfmt.py" and "// end clock" markers) over a list of
// [seconds, unit] pairs and prints the results as JSON, so a test can
// compare them with aipager/clockfmt.py.
const fs = require("fs");
const page = fs.readFileSync(process.argv[2], "utf8");
const cases = JSON.parse(fs.readFileSync(process.argv[3], "utf8"));
const start = page.indexOf("// clock: port of aipager/clockfmt.py");
const end = page.indexOf("// end clock");
if (start < 0 || end < start) {
  console.error("the clock port's markers were not found");
  process.exit(2);
}
const clockText = new Function(page.slice(start, end) + "\nreturn clockText;")();
console.log(JSON.stringify(cases.map((c) => clockText(c[0], c[1]))));
