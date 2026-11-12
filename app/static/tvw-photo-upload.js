/* Resize before transfer so camera photos do not hit server/proxy upload limits. */
(() => {
  const resize = async file => {
    if (!['image/jpeg','image/png','image/webp'].includes(file.type)) throw new Error('Choose a JPG, PNG or WebP photo.');
    if (file.size > 50 * 1024 * 1024) throw new Error('This file is unusually large. Choose a photo below 50 MB.');
    let source, sourceUrl;
    try {
      if (typeof createImageBitmap === 'function') source = await createImageBitmap(file,{imageOrientation:'from-image'});
      else {
        sourceUrl=URL.createObjectURL(file);source=new Image();
        await new Promise((resolve,reject)=>{source.onload=resolve;source.onerror=()=>reject(new Error('This photo could not be opened. Try another JPG, PNG or WebP.'));source.src=sourceUrl;});
      }
      const width=source.naturalWidth||source.width,height=source.naturalHeight||source.height;
      if (!(width>0&&height>0)) throw new Error('This photo has no readable image dimensions.');
      if (Math.max(width,height)<=1600 && file.size<=768*1024) return file;
      const ratio=Math.min(1,1600/Math.max(width,height)),canvas=document.createElement('canvas');
      canvas.width=Math.max(1,Math.round(width*ratio));canvas.height=Math.max(1,Math.round(height*ratio));
      const ctx=canvas.getContext('2d');if(!ctx) throw new Error('Photo resizing is unavailable in this browser.');
      ctx.fillStyle='#fff';ctx.fillRect(0,0,canvas.width,canvas.height);ctx.drawImage(source,0,0,canvas.width,canvas.height);
      let blob;
      for(const quality of [.88,.76,.64,.52]) {
        blob=await new Promise(resolve=>canvas.toBlob(resolve,'image/jpeg',quality));
        if(!blob)throw new Error('The browser could not prepare this photo.');
        if(blob.size<=768*1024)break;
      }
      if(blob.size>768*1024) {
        const smaller=document.createElement('canvas');smaller.width=Math.max(1,Math.round(canvas.width/2));smaller.height=Math.max(1,Math.round(canvas.height/2));
        smaller.getContext('2d').drawImage(canvas,0,0,smaller.width,smaller.height);
        blob=await new Promise(resolve=>smaller.toBlob(resolve,'image/jpeg',.76));
        if(!blob || blob.size>768*1024)throw new Error('The browser could not prepare this photo.');
      }
      return new File([blob],file.name.replace(/\.[^.]+$/,'')+'-optimized.jpg',{type:'image/jpeg',lastModified:Date.now()});
    } finally { source?.close?.();if(sourceUrl)URL.revokeObjectURL(sourceUrl); }
  };
  window.TVWPhotoUpload={resize};
})();
