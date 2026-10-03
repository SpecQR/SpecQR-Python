// SPDX-License-Identifier: MIT
// Adapted from SpecQR-CPP tools/reference.mjs at e91cd8.
// Development-only oracle. Imports the owner's pinned JS checkout plus the
// separately installed, pinned Nayuki package; neither is part of the Python runtime.
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {createHash} from 'node:crypto';
import {execFileSync} from 'node:child_process';
import {createRequire} from 'node:module';
import {resolve, join} from 'node:path';
import {pathToFileURL,fileURLToPath} from 'node:url';
const suite = process.argv.find(a=>a.startsWith('--suite='))?.split('=')[1] ?? 'public';
const baseline=resolve(process.argv[2]??'');
const expectedCommit='15ad15e5c770ea0e39072f8f88b2733018f02ffd';
assert.equal(execFileSync('git',['-C',baseline,'rev-parse','HEAD'],{encoding:'utf8'}).trim(),expectedCommit);
execFileSync('git',['-C',baseline,'diff','--quiet','HEAD','--','src','package.json']);
const load=p=>import(pathToFileURL(join(baseline,p)).href);
const api=await load('src/index.js');
const {normalizeOptions}=await load('src/options.js');
const {selectPlanForInput,selectPlanForManualSegments}=await load('src/internal/planning.js');
const {normalizeManualSegments,encodeSegments}=await load('src/encoding/modes.js');
const {buildResultArtifact}=await load('src/internal/build.js');
const tables=await load('src/core/tables.js');
const {interleaveCodewords}=await load('src/core/codewords.js');
const {buildMatrix}=await load('src/core/matrix.js');
const {multiply}=await load('src/core/galois-field.js');
const {createGeneratorPolynomial,computeRemainder}=await load('src/core/reed-solomon.js');
const hash=bytes=>createHash('sha256').update(bytes).digest('hex');
const matrixHash=m=>hash(m.map(r=>r.map(Number).join('')).join(''));
const hex=bytes=>Buffer.from(bytes).toString('hex');
const emit=(request,expected,extra={})=>console.log(JSON.stringify({request,expected,...extra}));
if(['internal','internal-smoke'].includes(suite)) {
 for(let version=1;version<=40;version++)for(const [ordinal,ecc] of ['L','M','Q','H'].entries())for(let seed=0;seed<3;seed++) {
  const capacity=tables.getDataCodewordCount(version,ecc);
  const data=Array.from({length:capacity},(_,i)=>seed===0?0:seed===1?255:((i*149+version*43+ordinal*89+seed*67)^(i>>(seed+1)))&255);
  const codewords=interleaveCodewords(data,version,ecc).codewords;
  for(let mask=-1;mask<8;mask++) {
   const result=buildMatrix(codewords,version,ecc,mask<0?'auto':mask);
   emit({command:'raw',version,ecc,seed,mask},{data:hex(data),codewords:hex(codewords),matrixHash:matrixHash(result.matrix),mask:result.maskPattern,penalty:result.penalty,penalties:result.maskPenalties.map(p=>p.penalty)}); if(suite === 'internal-smoke') process.exit(0);
  }
 }
 const products=[];for(let a=0;a<256;a++)for(let b=0;b<256;b++)products.push(multiply(a,b));
 emit({command:'gf'},{bytes:hex(products)});
 for(let degree=1;degree<=255;degree++) {const generator=createGeneratorPolynomial(degree), data=Array.from({length:300},(_,i)=>(i*61+degree)&255);emit({command:'rs',degree},{generator:hex(generator),remainder:hex(computeRemainder(data,generator))});}
 process.exit(0);
}
const require=createRequire(join(resolve(process.env.SPECQR_DEV_NODE_MODULES??fileURLToPath(new URL('.',import.meta.url))),'package.json'));
const imported=require('nayuki-qr-code-generator');const nayuki=imported.default??imported;
assert.equal(require('nayuki-qr-code-generator/package.json').version,'1.8.0');
const eccMap={L:nayuki.QrCode.Ecc.LOW,M:nayuki.QrCode.Ecc.MEDIUM,Q:nayuki.QrCode.Ecc.QUARTILE,H:nayuki.QrCode.Ecc.HIGH};
function refSegments(r) {return (r.segments??(r.bytes?[{mode:'byte',bytes:r.bytes}]:[{mode:r.options.mode,text:r.text}])).map(s=>{
 switch(s.mode) {case'numeric':return nayuki.QrSegment.makeNumeric(s.text);case'alphanumeric':return nayuki.QrSegment.makeAlphanumeric(s.text);case'byte':return nayuki.QrSegment.makeBytes(s.bytes??Array.from(new TextEncoder().encode(s.text)));case'eci':return nayuki.QrSegment.makeEci(s.assignmentNumber);default:return null;}
});}
function add(r,corrections={}) {
 const opts=normalizeOptions({...r.options,...corrections,output:'matrix',diagnostics:true});
 const input=r.bytes?Uint8Array.from(r.bytes):r.text??'';
 const segments=r.segments?normalizeManualSegments(r.segments.map(s=>['numeric','alphanumeric','byte','kanji'].includes(s.mode)?{mode:s.mode,data:s.bytes??s.text}:s)):null;
 const plan=segments?selectPlanForManualSegments(segments,opts):selectPlanForInput(input,opts);
 const built=buildResultArtifact(plan,opts);const result=segments?api.generateSegments(r.segments.map(s=>['numeric','alphanumeric','byte','kanji'].includes(s.mode)?{mode:s.mode,data:s.bytes??s.text}:s),opts):api.generate(input,opts);
 let independent=false;
 if(opts.version!=='auto'&&opts.maskPattern!=='auto'&&!opts.gs1&&opts.fnc1Second===false&&opts.eci===false) {
  const ref=refSegments(r);if(ref.every(Boolean)) {const q=nayuki.QrCode.encodeSegments(ref,eccMap[opts.errorCorrectionLevel],opts.version,opts.version,opts.maskPattern,false);const rows=Array.from({length:q.size},(_,y)=>Array.from({length:q.size},(_,x)=>q.getModule(x,y)?1:0));assert.equal(matrixHash(rows),matrixHash(result.matrix));independent=true;}
 }
 emit(r,{version:plan.version,ecc:plan.errorCorrectionLevel,mask:result.maskPattern??result.diagnostics.maskPattern,data:hex(encodeSegments(plan.segments,plan.version,plan.errorCorrectionLevel)),codewords:hex(built.interleaved.codewords),matrixHash:matrixHash(result.matrix)}, {independent});
}
if (suite === 'decode-smoke') { add({text:'HELLO',options:{mode:'byte',version:1,errorCorrectionLevel:'M',maskPattern:0}}); process.exit(0); }
const kinds=[{text:'123456789',options:{mode:'numeric'}},{text:'HELLO:1',options:{mode:'alphanumeric'}},{text:'aé',options:{mode:'byte'}},{bytes:[0,1,127,128,254,255]},{segments:[{mode:'numeric',text:'123'},{mode:'byte',bytes:[97]}]},{segments:[{mode:'eci',assignmentNumber:26},{mode:'byte',text:'雪'}]},{text:'漢字',options:{mode:'kanji'}},{segments:[{mode:'fnc1-second',applicationIndicator:'A'},{mode:'alphanumeric',text:'ABC'}]}];
for(let version=1;version<=40;version++)for(const [ei,errorCorrectionLevel]of ['L','M','Q','H'].entries())for(let maskPattern=0;maskPattern<8;maskPattern++){const kind=kinds[(version+ei+maskPattern)%kinds.length];add({...kind,id:`fixed-v${version}-${errorCorrectionLevel}-m${maskPattern}`,options:{...kind.options,version,errorCorrectionLevel,maskPattern}});}
// Independent oracle coverage for every version/ECC/mask tuple.
for(let version=1;version<=40;version++)for(const errorCorrectionLevel of ['L','M','Q','H'])for(let maskPattern=0;maskPattern<8;maskPattern++)add({id:`nayuki-all-v${version}-${errorCorrectionLevel}-m${maskPattern}`,bytes:[0,1,127,128,254,255],options:{version,errorCorrectionLevel,maskPattern}});
for(const text of ['', 'A','HELLO WORLD','https://example.com/a?q=1','123456789012345678901234567890','日本語漢字かなカナ','abc12345678901234567890XYZ','😀e\u0301é','𝄞𐐷0\0\x7f','12A34B56C78D90','x'.repeat(300),'1'.repeat(1000)])for(const errorCorrectionLevel of ['L','M','Q','H'])for(const optimizeSegments of [true,false])add({text,options:{errorCorrectionLevel,optimizeSegments}});
for(const version of [1,2,9,10,26,27,40])for(const errorCorrectionLevel of ['L','M','Q','H'])add({text:'A1',options:{version,errorCorrectionLevel,boostErrorCorrection:true}});
for(const assignmentNumber of [0,127,128,16383,16384,999999])add({segments:[{mode:'eci',assignmentNumber},{mode:'byte',text:'ECI'}]});
for(const indicator of ['00','99','A','z'])add({text:'ABC',options:{fnc1Second:indicator}});
for(const text of ['0109501101530003','010950110153000310LOT123\x1d17271231'])add({text,options:{gs1:true}});
for(const headers of [{gs1:true},{fnc1Second:'A'}])for(const optimizeSegments of [true,false])for(const text of ['10ABC%DEF','10ABC%%DEF','10LOT%\x1d21SER%IAL'])add({text,options:{...headers,optimizeSegments}},{mode:'byte'});
let seed=0x5eec0de;const random=()=>{seed^=seed<<13;seed^=seed>>>17;seed^=seed<<5;return seed>>>0;};const alphabet=Array.from('ABCXYZ0123456789abcé漢字😀%:');
for(let i=0;i<160;i++){let text='';for(let j=random()%60;j>0;j--)text+=alphabet[random()%alphabet.length];add({id:`fuzz-${i}`,text,options:{errorCorrectionLevel:['L','M','Q','H'][i%4],optimizeSegments:i%2===0}});}
for(let version=1;version<=40;version++)for(const errorCorrectionLevel of ['L','M','Q','H'])for(const mode of ['numeric','alphanumeric','byte','kanji']) {
 const options={version,errorCorrectionLevel,mode};const capacity=api.getCapacity(options);const maximum=capacity.maxCharacters??capacity.maxBytes;
 emit({command:'capacity',options},{maximum,dataCodewords:capacity.dataCodewords,capacityBits:capacity.capacityBits,countBits:capacity.characterCountBits});
 for(const count of [maximum-1,maximum,maximum+1]){const text={numeric:'1',alphanumeric:'A',byte:'a',kanji:'漢'}[mode].repeat(count);const estimate=api.estimate(text,options);emit({command:'estimate',text,options},{fits:estimate.ok,version:estimate.diagnostics.version,requiredBits:estimate.dataBitLength,capacityBits:estimate.capacityBits});}
}

for(const request of JSON.parse(readFileSync(new URL('./structured-append-cases.json',import.meta.url))).cases) {
 const options={...request.options,output:'matrix',diagnostics:true};
 const result=request.segments?api.generateSegmentsStructuredAppend(request.segments.map(s=>['numeric','alphanumeric','byte','kanji'].includes(s.mode)?{mode:s.mode,data:s.bytes??s.text??s.data}:s),options):api.generateStructuredAppend(request.bytes?Uint8Array.from(request.bytes):request.text,options);
 emit(request,{total:result.total,parity:result.parity,inputLength:result.inputLength,byteLength:result.byteLength,matrixHashes:result.symbols.map(s=>matrixHash(s.matrix)),versions:result.symbols.map(s=>s.diagnostics.version),masks:result.symbols.map(s=>s.diagnostics.maskPattern)});
}
