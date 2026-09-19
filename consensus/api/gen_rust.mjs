// Generate Rust types from the JSON Schema bundle derived from asyncapi.yaml.
//
// The AsyncAPI CLI's `generate models` only accepts AsyncAPI documents and its
// Rust generator refuses the root document, so we drive Modelina directly with
// the JSON Schema bundle instead. Same library the CLI uses underneath.
import { RustFileGenerator, RUST_DEFAULT_PRESET } from '@asyncapi/modelina';
import { readFileSync } from 'fs';

const schemaPath = process.argv[2];
const outDir = process.argv[3];
if (!schemaPath || !outDir) {
  console.error('usage: node gen_rust.mjs <schemas.json> <outdir>');
  process.exit(1);
}

const doc = JSON.parse(readFileSync(schemaPath, 'utf8'));

const generator = new RustFileGenerator({
  presets: [RUST_DEFAULT_PRESET],
});

// Generate one module per named definition so each payload type is a real
// struct/enum rather than collapsing into an opaque root.
const defs = doc.definitions ?? {};
let total = 0;
for (const [name, schema] of Object.entries(defs)) {
  const standalone = {
    $schema: 'http://json-schema.org/draft-07/schema#',
    title: name,
    definitions: defs,
    ...schema,
  };
  try {
    const models = await generator.generateToFiles(standalone, outDir, {
      moduleName: name,
    });
    total += models.length;
    console.log(`  ${name}: ${models.length} model(s)`);
  } catch (err) {
    console.error(`  ${name}: FAILED ${err.message}`);
  }
}
console.log(`generated ${total} model files into ${outDir}`);
