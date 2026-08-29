// Encodes the messages a real TV sends, using protobufjs + the real .proto.
// The Python mock TV replays these bytes so the Python decoder is validated
// against a genuine protobuf encoder rather than against itself.
const protobuf = require("protobufjs");
const fs = require("fs");
const path = require("path");

const root = protobuf.loadSync(path.join(__dirname, "..", "proto", "remotemessage.proto"));
const RemoteMessage = root.lookupType("remote.RemoteMessage");

function enc(payload) {
  const err = RemoteMessage.verify(payload);
  if (err) throw new Error(err);
  return Buffer.from(RemoteMessage.encodeDelimited(RemoteMessage.create(payload)).finish())
    .toString("hex");
}

const fixtures = {
  configure: enc({
    remoteConfigure: {
      code1: 622,
      deviceInfo: {
        model: "Chromecast", vendor: "Google", unknown1: 1, unknown2: "1",
        packageName: "com.google.android.tv.remote.service", appVersion: "5.0",
      },
    },
  }),
  set_active: enc({ remoteSetActive: { active: 622 } }),
  ping: enc({ remotePingRequest: { val1: 42, val2: 1 } }),
  start_on: enc({ remoteStart: { started: true } }),
  volume: enc({
    remoteSetVolumeLevel: {
      playerModel: "Living Room TV", volumeMax: 100, volumeLevel: 37, volumeMuted: false,
    },
  }),
  current_app: enc({
    remoteImeKeyInject: {
      appInfo: { appPackage: "com.google.android.youtube.tv" },
      textFieldStatus: { counterField: 1, value: "", label: "Search" },
    },
  }),
  // Sent by the TV when a text field gains focus; the counters must be
  // echoed back when typing.
  ime_batch_edit: enc({
    remoteImeBatchEdit: {
      imeCounter: 3, fieldCounter: 7,
      editInfo: [{ insert: 2, textFieldStatus: { start: 2, end: 2, value: "hi" } }],
    },
  }),
  ime_show_request: enc({
    remoteImeShowRequest: { remoteTextFieldStatus: { counterField: 7, value: "", label: "Search" } },
  }),
  // A message long enough that its length prefix needs a 2-byte varint,
  // which is where a naive single-byte framing reader breaks.
  long_app_link: enc({ remoteAppLinkLaunchRequest: { appLink: "https://example.com/" + "x".repeat(300) } }),
};

fs.writeFileSync(path.join(__dirname, "fixtures.json"), JSON.stringify(fixtures, null, 1));
console.log("wrote " + Object.keys(fixtures).length + " fixtures");
