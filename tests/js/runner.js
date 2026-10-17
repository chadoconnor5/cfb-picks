// Reads [{fn, args}] as JSON on stdin, prints [{ok, value} | {ok: false, error}] (used by test_js_parity.py).
const L = require("../../docs/logic.js");
const fns = {
  ...L,
  candidateConfigs: (base) => [...L.candidateConfigs(base)],
  answersByItem: (d) => [...L.answersByItem(d).entries()],
};
let input = "";
process.stdin.on("data", (c) => (input += c));
process.stdin.on("end", () => {
  const out = JSON.parse(input).map(({ fn, args }) => {
    try { const v = fns[fn](...args); return { ok: true, value: v === undefined ? null : v }; }
    catch (e) { return { ok: false, error: e.message }; }
  });
  process.stdout.write(JSON.stringify(out));
});
