// Owned production entry. No injected SDK/client/results, no CLI execution on import.
import {predictOwnedNative} from './lms_native_prediction.mjs';
import {pathToFileURL} from 'node:url';
const MAX_INPUT = 65536;
const MAX_OUTPUT = 1048576;
export async function main() {
  try {
    const chunks = []; let size = 0;
    for await (const chunk of process.stdin) {
      size += chunk.length;
      if (size > MAX_INPUT) throw new Error('input bound');
      chunks.push(chunk);
    }
    const raw = new TextDecoder('utf-8', {fatal: true}).decode(Buffer.concat(chunks));
    const request = JSON.parse(raw);
    // Parent supplies one compact UTF-8 JSON object. Reject duplicate/trailing forms.
    if (JSON.stringify(request) !== raw) throw new Error('noncanonical input');
    if (!Number.isSafeInteger(request.generationDeadlineUnixMs)) throw new Error("missing parent deadline");
    const result = await predictOwnedNative(request);
    const output = JSON.stringify(result);
    if (Buffer.byteLength(output) > MAX_OUTPUT) throw new Error('output bound');
    process.stdout.write(output);
  } catch {
    process.stdout.write(JSON.stringify({status: 'ENTRY_REFUSED', cleanup: 'UNKNOWN'}));
    process.exitCode = 2;
  }
}
if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) await main();
