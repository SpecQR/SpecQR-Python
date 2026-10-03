// Independent, development-only Nayuki controls for the ZXing-C++ ECI API.
import {createRequire} from 'node:module';
import {resolve,join} from 'node:path';
import {fileURLToPath} from 'node:url';
import assert from 'node:assert/strict';
const require=createRequire(join(resolve(process.env.SPECQR_DEV_NODE_MODULES??fileURLToPath(new URL('.',import.meta.url))),'package.json'));
const pkg=require('nayuki-qr-code-generator');const qr=pkg.default??pkg;
assert.equal(require('nayuki-qr-code-generator/package.json').version,'1.8.0');
for(const [assignment,bytes,text]of [[3,[99,97,102,233],'café'],[20,[138,191,142,154],'漢字'],[26,Array.from(new TextEncoder().encode('café🙂漢字')),'café🙂漢字'],[170,[65,83,67,73,73],'ASCII']]) {
 const q=qr.QrCode.encodeSegments([qr.QrSegment.makeEci(assignment),qr.QrSegment.makeBytes(bytes)],qr.QrCode.Ecc.MEDIUM,4,4,0,false);
 console.log(JSON.stringify({assignment,bytes,text,matrix:Array.from({length:q.size},(_,y)=>Array.from({length:q.size},(_,x)=>q.getModule(x,y)?'1':'0').join(''))}));
}
