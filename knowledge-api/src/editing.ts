/** Editor-neutral operations; the HTTP API remains the authorization boundary. */
// Barrel: implementation lives in ./editing/* (contract, operations, HTTP wiring).
export * from "./editing/contract";
export { sha256 } from "./editing/hash";
export { installEditing } from "./editing/http";
export { applyEdits } from "./editing/patch";
export { DocumentEditing } from "./editing/service";
