const assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs');
let dimensions={width:6000,height:4000},closed=0,canvas,blobs=[100000],qualityCalls=[];
class File {constructor(parts,name,options){this.name=name;this.type=options.type;this.size=parts.reduce((n,p)=>n+p.size,0);}}
const ctx={window:{},File,createImageBitmap:async()=>({...dimensions,close:()=>closed++}),document:{createElement:()=>{canvas={width:0,height:0,getContext:()=>({fillRect(){},drawImage(){}}),toBlob:(callback,type,quality)=>{qualityCalls.push(quality);callback({size:blobs.shift()||100000});}};return canvas;}},URL:{}};
vm.runInNewContext(fs.readFileSync('app/static/tvw-photo-upload.js','utf8'),ctx);
(async()=>{
 const resize=ctx.window.TVWPhotoUpload.resize;
 const large={name:'camera.png',type:'image/png',size:12000000};
 const output=await resize(large);assert.equal(output.type,'image/jpeg');assert.equal(canvas.width,1600);assert.equal(canvas.height,1067);assert.equal(large.size,12000000);assert.equal(closed,1);
 dimensions={width:800,height:600};const small={name:'small.jpg',type:'image/jpeg',size:100000};assert.equal(await resize(small),small);
 dimensions={width:4000,height:6000};await resize(large);assert.equal(canvas.width,1067);assert.equal(canvas.height,1600);
 blobs=[1500000,1100000,600000];qualityCalls=[];const compressed=await resize(large);assert.equal(qualityCalls.length,3);assert.ok(compressed.size<=768*1024);
 await assert.rejects(()=>resize({type:'image/svg+xml',size:100}),/JPG/);
 await assert.rejects(()=>resize({type:'image/jpeg',size:51*1024*1024}),/50 MB/);
 console.log('Photo resize passed: landscape/portrait scaling, original preservation, small-file pass-through, adaptive compression, format and resource limits.');
})().catch(error=>{console.error(error);process.exitCode=1;});
