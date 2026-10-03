// Independent test-only decoder: consumes Python matrices, never an encoder.
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {resolve,join} from 'node:path';
import {fileURLToPath} from 'node:url';
import readline from 'node:readline';
const require=createRequire(join(resolve(process.env.SPECQR_DEV_NODE_MODULES??fileURLToPath(new URL('.',import.meta.url))),'package.json'));
const jsQR=require('jsqr');
assert.equal(require('jsqr/package.json').version,'1.4.0');
const lines=readline.createInterface({input:process.stdin});
for await(const line of lines) {
 const r=JSON.parse(line), scale=r.scale??4, width=r.width??((r.matrix.length+8)*scale);
 const luminance=r.luminanceHex?Buffer.from(r.luminanceHex,'hex'):null;
 if(luminance)assert.equal(luminance.length,width*width);
 const data=new Uint8ClampedArray(width*width*4);
 for(let y=0;y<width;y++)for(let x=0;x<width;x++) {
  const my=Math.floor(y/scale)-4,mx=Math.floor(x/scale)-4;
  const dark=!luminance&&my>=0&&my<r.matrix.length&&mx>=0&&mx<r.matrix.length&&r.matrix[my][mx]==='1';
  const i=(y*width+x)*4;data[i]=data[i+1]=data[i+2]=luminance?luminance[y*width+x]:(dark?0:255);data[i+3]=255;
 }
 const result=jsQR(data,width,width,{inversionAttempts:'dontInvert'});
 console.log(JSON.stringify(result===null?{error:'NoSymbol'}:{text:result.data,bytes:result.binaryData,version:result.version,chunks:result.chunks}));
}
