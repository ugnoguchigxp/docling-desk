export const sha256 = (s: string | Uint8Array) => Bun.CryptoHasher.hash("sha256", s, "hex");
