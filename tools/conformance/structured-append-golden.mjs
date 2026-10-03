// SPDX-License-Identifier: MIT
// Development-only golden regeneration; no Python runtime dependency.
// Usage: node structured-append-golden.mjs BASELINE CASES_JSON OUTPUT_JSON
import {readFileSync,writeFileSync} from 'node:fs';
import {pathToFileURL} from 'node:url';
import {resolve,join} from 'node:path';
import {execFileSync} from 'node:child_process';
const baseline=resolve(process.argv[2]);
const pinned='15ad15e5c770ea0e39072f8f88b2733018f02ffd';
if(execFileSync('git',['-C',baseline,'rev-parse','HEAD'],{encoding:'utf8'}).trim()!==pinned)throw Error('baseline mismatch');
execFileSync('git',['-C',baseline,'diff','--quiet','HEAD','--','src','package.json']);
const load=p=>import(pathToFileURL(join(baseline,p)).href);
const api=await load('src/index.js');
const {normalizeOptions}=await load('src/options.js');
const {selectPlanForInput,selectPlanForManualSegments}=await load('src/internal/planning.js');
const {normalizeManualSegments,encodeSegments}=await load('src/encoding/modes.js');
const {interleaveCodewords}=await load('src/core/codewords.js');
const requests=JSON.parse(readFileSync(process.argv[3])).cases;
const rows=m=>m.map(row=>row.map(Number).join(''));
const convertSegments=segments=>segments.map(s=>({...s,data:s.bytes?Uint8Array.from(s.bytes):s.text??s.data}));
const cases=[];
for(const request of requests){
 const manual=!!request.segments;
 const raw=manual?convertSegments(request.segments):request.bytes?Uint8Array.from(request.bytes):request.text;
 const opts={...request.options,output:'matrix',diagnostics:manual?{splitUnits:'full'}:true};
 const result=manual?api.generateSegmentsStructuredAppend(raw,opts):api.generateStructuredAppend(raw,opts);
 const symbols=result.symbols.map((symbol,index)=>{
  const diag=result.diagnostics.symbols[index];
  const options=normalizeOptions({...request.options,version:diag.version,structuredAppend:{index:index+1,total:result.total,parity:result.parity},diagnostics:true,output:'matrix'});
  let plan;
  if(manual){
   const units=result.diagnostics.splitUnits.slice(diag.splitUnitStart,diag.splitUnitStart+diag.splitUnitLength);
   const parts=[];
   for(const unit of units){
    const source=raw[unit.sourceSegmentIndex];
    const binary=source.data instanceof Uint8Array;
    const data=source.mode!=='byte'?source.data:binary?source.data.slice(unit.unitStart,unit.unitStart+unit.unitLength):Array.from(source.data).slice(unit.unitStart,unit.unitStart+unit.unitLength).join('');
    const last=parts.at(-1);
    if(last&&last.source===unit.sourceSegmentIndex){last.data=binary?Uint8Array.from([...last.data,...data]):last.data+data;}
    else parts.push({source:unit.sourceSegmentIndex,mode:source.mode,data});
   }
   plan=selectPlanForManualSegments(normalizeManualSegments(parts.map(({mode,data})=>({mode,data}))),options);
  }else{
   const chunk=typeof raw==='string'?Array.from(raw).slice(diag.inputStart,diag.inputStart+diag.inputLength).join(''):raw.slice(diag.inputStart,diag.inputStart+diag.inputLength);
   plan=selectPlanForInput(chunk,options);
  }
  const data=encodeSegments(plan.segments,plan.version,plan.errorCorrectionLevel);
  return {matrix:rows(symbol.matrix),data_codewords:Buffer.from(data).toString('hex'),codewords:Buffer.from(interleaveCodewords(data,plan.version,plan.errorCorrectionLevel).codewords).toString('hex')};
 });
 cases.push({request,expected:{total:result.total,parity:result.parity,inputLength:result.inputLength,byteLength:result.byteLength,diagnostics:result.diagnostics,symbols}});
}
writeFileSync(process.argv[4],JSON.stringify({baseline:pinned,cases}));
console.log(cases.length,cases.reduce((n,c)=>n+c.expected.total,0));
