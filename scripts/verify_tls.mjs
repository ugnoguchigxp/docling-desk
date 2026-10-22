// Run with Node or Bun. Sends no credentials or application data.
import https from 'node:https';

const bad = ['dv-g3b-emudhra.we-have-a-problem.com', 'dv-g3b-aaa-emudhra.we-have-a-problem.com'];
const good = ['registry.npmjs.org', 'nodejs.org'];
const certErrors = new Set([
  'UNABLE_TO_GET_ISSUER_CERT_LOCALLY', 'UNABLE_TO_VERIFY_LEAF_SIGNATURE',
  'CERT_REVOKED', 'CERT_UNTRUSTED', 'SELF_SIGNED_CERT_IN_CHAIN',
]);
console.log(JSON.stringify({node: process.version, bun: process.versions.bun, openssl: process.versions.openssl}));
let failed = false;
for (const client of ['https', 'fetch']) {
  for (const host of [...good, ...bad]) {
    let outcome;
    let code;
    try {
      if (client === 'https') {
        await new Promise((resolve, reject) => {
          const req = https.get(`https://${host}/`, {timeout: 10000}, res => {
            res.resume();
            resolve();
          });
          req.on('error', reject);
          req.on('timeout', () => req.destroy(new Error('timeout')));
        });
      } else {
        const res = await fetch(`https://${host}/`, {signal: AbortSignal.timeout(10000), redirect: 'manual'});
        await res.body?.cancel();
      }
      outcome = 'accepted';
    } catch (error) {
      code = error.cause?.code ?? error.code;
      outcome = certErrors.has(code) ? 'certificate_rejected' : 'network_or_other_error';
    }
    const ok = outcome === (bad.includes(host) ? 'certificate_rejected' : 'accepted');
    console.log(JSON.stringify({client, host, outcome, code, ok}));
    failed ||= !ok;
  }
}
process.exitCode = failed ? 1 : 0;
