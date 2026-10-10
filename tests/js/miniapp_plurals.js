// Runs the Mini App's own plural rules (the lines between its "// plurals:
// port of aipager/i18n/plurals.py" and "// end plurals" markers) over a
// list of [code, n] pairs and prints the categories as JSON, so a test can
// compare them with aipager/i18n/plurals.py.
const fs = require("fs");
const page = fs.readFileSync(process.argv[2], "utf8");
const cases = JSON.parse(fs.readFileSync(process.argv[3], "utf8"));
const start = page.indexOf("// plurals: port of aipager/i18n/plurals.py");
const end = page.indexOf("// end plurals");
if (start < 0 || end < start) {
  console.error("the plural port's markers were not found");
  process.exit(2);
}
const pluralCategory = new Function(page.slice(start, end) + "\nreturn pluralCategory;")();
console.log(JSON.stringify(cases.map((c) => pluralCategory(c[0], c[1]))));
