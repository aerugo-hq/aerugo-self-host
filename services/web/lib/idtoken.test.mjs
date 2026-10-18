// L3: a sign-in's ID token is accepted only when the identity provider signed it, for this app and this sign-in, and it
// hasn't expired. Run: node --test "lib/*.test.mjs"   (make test-web)
import assert from "node:assert/strict";
import crypto from "node:crypto";
import test from "node:test";
import { UnknownKey, verifyIdToken } from "./idtoken.mjs";

const { publicKey, privateKey } = crypto.generateKeyPairSync("rsa", { modulusLength: 2048 });
const keys = [{ ...publicKey.export({ format: "jwk" }), kid: "realm-key", use: "sig", alg: "RS256" }];
const expected = { issuer: "https://auth.acme.test/realms/acme", audience: "kl-web", nonce: "n-123", now: 1_800_000_000 };
const b64 = (value) => Buffer.from(JSON.stringify(value)).toString("base64url");

function token(claims = {}, header = {}, key = privateKey) {
  const body = `${b64({ alg: "RS256", kid: "realm-key", typ: "JWT", ...header })}.${b64({
    iss: expected.issuer, aud: "kl-web", exp: expected.now + 300, nonce: "n-123", sub: "alice", ...claims })}`;
  return `${body}.${crypto.sign("RSA-SHA256", Buffer.from(body), key).toString("base64url")}`;
}

test("a token the identity provider signed, for this app and this sign-in, is accepted", () => {
  assert.equal(verifyIdToken(token(), keys, expected).sub, "alice");
});

test("a token whose claims were changed after signing is refused", () => {
  const [header, , signature] = token().split(".");
  assert.throws(() => verifyIdToken(`${header}.${b64({ ...expected, aud: "kl-web", sub: "olivia", exp: expected.now + 300 })}.${signature}`,
    keys, expected), /signature/);
});

test("a token signed by any other key is refused", () => {
  const other = crypto.generateKeyPairSync("rsa", { modulusLength: 2048 }).privateKey;
  assert.throws(() => verifyIdToken(token({}, {}, other), keys, expected), /signature/);
});

test("a token signed with a key that isn't published asks for the keys again", () => {
  assert.throws(() => verifyIdToken(token({}, { kid: "rotated" }), keys, expected), UnknownKey);
});

test("an unsigned token, or one signed another way, is refused", () => {
  const [header, payload] = token().split(".");
  assert.throws(() => verifyIdToken(`${b64({ alg: "none", kid: "realm-key" })}.${payload}.`, keys, expected), /not RS256/);
  assert.throws(() => verifyIdToken(`${b64({ alg: "HS256", kid: "realm-key" })}.${payload}.sig`, keys, expected), /not RS256/);
  assert.throws(() => verifyIdToken(`${header}.${payload}`, keys, expected), /not a signed token/);
});

test("another issuer, another app, an expired token, or another sign-in is refused", () => {
  assert.throws(() => verifyIdToken(token({ iss: "https://evil.test/realms/acme" }), keys, expected), /someone else/);
  assert.throws(() => verifyIdToken(token({ aud: "kl-api" }), keys, expected), /another app/);
  assert.throws(() => verifyIdToken(token({ exp: expected.now - 120 }), keys, expected), /expired/);
  assert.throws(() => verifyIdToken(token({ nonce: "n-999" }), keys, expected), /another sign-in/);
  assert.throws(() => verifyIdToken(token(), keys, { ...expected, nonce: undefined }), /another sign-in/);
});

test("a token naming this app among several audiences is accepted", () => {
  assert.equal(verifyIdToken(token({ aud: ["kl-web", "account"] }), keys, expected).sub, "alice");
});
