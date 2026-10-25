import { ReplitConnectors } from "@replit/connectors-sdk";

const connectors = new ReplitConnectors();

function encodeHeader(value) {
  return `=?UTF-8?B?${Buffer.from(value, "utf8").toString("base64")}?=`;
}

function wrapBase64(value) {
  return value.match(/.{1,76}/g)?.join("\r\n") ?? "";
}

async function readPayload() {
  let input = "";
  for await (const chunk of process.stdin) {
    input += chunk;
  }

  const payload = JSON.parse(input);
  if (
    typeof payload.subject !== "string" ||
    typeof payload.body !== "string" ||
    !payload.subject ||
    !payload.body
  ) {
    throw new Error("Expected a message subject and body.");
  }
  return payload;
}

async function main() {
  const { subject, body } = await readPayload();

  const profileResponse = await connectors.proxy(
    "google-mail",
    "/gmail/v1/users/me/profile",
    { method: "GET" },
  );
  if (!profileResponse.ok) {
    throw new Error(`Could not read the connected Gmail profile (HTTP ${profileResponse.status}).`);
  }

  const profile = await profileResponse.json();
  const emailAddress = profile.emailAddress;
  if (typeof emailAddress !== "string" || !emailAddress) {
    throw new Error("The connected Gmail profile did not return an email address.");
  }

  const encodedBody = wrapBase64(Buffer.from(body, "utf8").toString("base64"));
  const rawMessage = [
    `To: ${emailAddress}`,
    `From: ${emailAddress}`,
    `Subject: ${encodeHeader(subject)}`,
    `Date: ${new Date().toUTCString()}`,
    "MIME-Version: 1.0",
    "Content-Type: text/plain; charset=UTF-8",
    "Content-Transfer-Encoding: base64",
    "",
    encodedBody,
  ].join("\r\n");
  const raw = Buffer.from(rawMessage, "utf8").toString("base64url");

  const sendResponse = await connectors.proxy(
    "google-mail",
    "/gmail/v1/users/me/messages/send",
    { method: "POST", body: { raw } },
  );
  if (!sendResponse.ok) {
    throw new Error(`Gmail could not send the alert (HTTP ${sendResponse.status}).`);
  }

  process.stdout.write("Alert sent.\n");
}

main().catch((error) => {
  process.stderr.write(`${error.message}\n`);
  process.exitCode = 1;
});
