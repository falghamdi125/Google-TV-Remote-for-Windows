// Decodes the Python encoder's output with the real protobufjs + .proto files.
// Any mismatch in field numbers, wire types or framing shows up here.
const protobuf = require("protobufjs");
const fs = require("fs");
const path = require("path");

const pairingRoot = protobuf.loadSync(path.join(__dirname, "..", "proto", "pairingmessage.proto"));
const remoteRoot = protobuf.loadSync(path.join(__dirname, "..", "proto", "remotemessage.proto"));
const cases = JSON.parse(fs.readFileSync(path.join(__dirname, "cases.json")));

let pass = 0, fail = 0;
for (const c of cases) {
  const root = c.type.startsWith("pairing.") ? pairingRoot : remoteRoot;
  const Type = root.lookupType(c.type);
  const buf = Buffer.from(c.hex, "hex");
  let got;
  try {
    // decodeDelimited also proves our varint length framing is right
    const reader = protobuf.Reader.create(buf);
    const msg = Type.decodeDelimited(reader);
    if (reader.pos !== buf.length) throw new Error(`framing: ${reader.pos} of ${buf.length} bytes consumed`);
    got = Type.toObject(msg, { enums: String, bytes: String, defaults: false });
    if (got.pairingSecret && got.pairingSecret.secret)
      got.pairingSecret.secret = Buffer.from(got.pairingSecret.secret, "base64").toString("hex");
  } catch (e) {
    console.log(`FAIL ${c.name}: ${e.message}`);
    fail++; continue;
  }
  const a = JSON.stringify(got), b = JSON.stringify(c.expected);
  if (a === b) { console.log(`pass  ${c.name}`); pass++; }
  else { console.log(`FAIL  ${c.name}\n      got      ${a}\n      expected ${b}`); fail++; }
}
console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);
