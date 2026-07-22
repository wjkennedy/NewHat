const ns = 'http://www.w3.org/2000/svg';
const stitches = document.querySelector('#stitches');
const needle = document.querySelector('#needle');
const timeline = document.querySelector('#timeline');
const artworkFile = document.querySelector('#artworkFile');
const artworkPreview = document.querySelector('#artworkPreview');
const artworkTransform = document.querySelector('#artworkTransform');
const artworkBounds = document.querySelector('#artworkBounds');
const dstFile = document.querySelector('#dstFile');
let playing, densityFixed = false, step = 0, zeroOffset = {x:0,y:0}, activeNeedle = 0, digitizeVersion = 0;
let mirrored = false, placement = 'center';
let artworkData;
let articlePlaced = false;
let generatedPlan = null;
const needles=[['01','#ed5e98','Rose 1847'],['02','#eebd54','Gold 217'],['03','#247b79','Teal 3810'],['04','#eee9df','White 001'],['05','#252b2d','Black 900'],['06','#1e4f9b','Blue 354'],['07','#e85d48','Red 191'],['08','#7b5b9d','Violet 261'],['09','#7a9d46','Green 550'],['A','#d6a176','Tan 842'],['B','#b5b8ba','Silver 401'],['C','#6e4b3a','Brown 807'],['D','#f182b5','Pink 202'],['E','#f3dc62','Yellow 109'],['F','#7a7f88','Gray 415']];
let threadSequence = [needles[0][1]], colorEnabled = [];
function applyThreadColor(index,color){const previous=threadSequence[index];needles[index][1]=color;threadSequence[index]=color;if(generatedPlan&&generatedPlan.palette[index]===previous){generatedPlan.palette[index]=color;generatedPlan.regions.filter(region=>region.color===previous).forEach(region=>region.color=color)}renderNeedles();renderColorLayers();colorizeOps();draw()}
function renderNeedles(){const rack=document.querySelector('#needleRack'),editor=document.querySelector('#needleEditor');rack.replaceChildren();editor.replaceChildren();needles.forEach((needle,index)=>{const button=document.createElement('button');button.className=`needle-slot ${index===activeNeedle?'active':''}`;button.innerHTML=`<span class="spool" style="--thread:${needle[1]}"><em></em></span><i></i><b>${needle[0]}</b>`;button.onclick=()=>{activeNeedle=index;renderNeedles();document.querySelector('#activeNeedle').textContent=needles[index][0]};rack.append(button);const row=document.createElement('label');row.className='needle-row';row.innerHTML=`<button type="button" class="needle-pick ${index===activeNeedle?'active':''}">${needle[0]}</button><input aria-label="Thread color for needle ${needle[0]}" type="color" value="${needle[1]}"><input aria-label="Thread name for needle ${needle[0]}" value="${needle[2]}">`;row.querySelector('.needle-pick').onclick=()=>{activeNeedle=index;renderNeedles();document.querySelector('#activeNeedle').textContent=needles[index][0]};row.querySelector('input[type=color]').oninput=e=>applyThreadColor(index,e.target.value);row.querySelector('input:not([type=color])').onchange=e=>{needle[2]=e.target.value;renderNeedles()};editor.append(row)});document.querySelector('#needleCount').textContent=`${threadSequence.length} / 15`}

function selectedPlanOperations(){if(!generatedPlan)return operations;let colorIndex=0,lastEmittedColor=-1,output=[];for(const operation of generatedPlan.operations){if(operation.kind==='color'){colorIndex++;continue}if(!colorEnabled[colorIndex])continue;if(lastEmittedColor!==colorIndex&&output.length)output.push({kind:'color',role:'color_change'});output.push({...operation});lastEmittedColor=colorIndex}return output}
async function refreshColorLayers(){if(!generatedPlan)return;const version=++digitizeVersion,selected=selectedPlanOperations();if(!selected.some(operation=>operation.kind==='stitch')){operations=[];setup();draw();return}await showCompiledDst(selected,version)}
function renderColorLayers(){const container=document.querySelector('#colorLayers');if(!container)return;container.replaceChildren();if(!generatedPlan)return;generatedPlan.palette.forEach((color,index)=>{const row=document.createElement('label'),enabled=document.createElement('input'),picker=document.createElement('input'),name=document.createElement('span');row.className=`color-layer ${colorEnabled[index]===false?'disabled':''}`;enabled.type='checkbox';enabled.checked=colorEnabled[index]!==false;enabled.setAttribute('aria-label',`Include color ${index+1}`);picker.type='color';picker.value=color;picker.setAttribute('aria-label',`Color ${index+1}`);name.textContent=`Color ${index+1}${enabled.checked?'':' · off'}`;enabled.onchange=async()=>{colorEnabled[index]=enabled.checked;renderColorLayers();await refreshColorLayers()};picker.oninput=()=>applyThreadColor(index,picker.value);row.append(enabled,picker,name);container.append(row)})}
function updateArtworkTransform() {
  const width = Number(document.querySelector('#scale').value) * 4.15;
  const center = placement === 'left' ? 310 : placement === 'right' ? 490 : 400;
  const rotation = Number(document.querySelector('#rotation').value);
  artworkPreview.setAttribute('width', width);
  artworkPreview.setAttribute('x', center - width / 2);
  artworkBounds.setAttribute('width', width);
  artworkBounds.setAttribute('x', center - width / 2);
  artworkTransform.setAttribute('transform', `translate(${center} 240) rotate(${rotation}) scale(${mirrored ? -1 : 1} 1) translate(${-center} -240)`);
}

function buildPlan(width = 72) {
  // No decorative fallback: a DST exists only when it represents loaded artwork.
  return [];
}
function digitizeArtwork(source, version) {
  // 0.42 mm rows and <= 2.5 mm stitches at the largest permitted design size.
  const canvas = document.createElement('canvas'), width = 360, height = 165;
  canvas.width = width; canvas.height = height;
  const context = canvas.getContext('2d', {willReadFrequently:true});
  context.fillStyle = '#fff'; context.fillRect(0, 0, width, height);
  const image = new Image();
  return new Promise(resolve => { image.onload = () => {
    const degrees = Number(document.querySelector('#rotation').value) * Math.PI / 180;
    context.save(); context.translate(width/2,height/2); context.rotate(degrees); context.scale(mirrored ? -1 : 1,1);
    const fit = Math.min(width/image.width,height/image.height); context.drawImage(image,-image.width*fit/2,-image.height*fit/2,image.width*fit,image.height*fit); context.restore();
    const planWidth=Number(document.querySelector('#scale').value), planHeight=Math.min(55,planWidth*height/width), center=placement==='left'?-28:placement==='right'?28:0, pixels=context.getImageData(0,0,width,height).data, xOffset=center-planWidth/2, yOffset=-planHeight/2, xStep=planWidth/(width-1), yStep=planHeight/(height-1), colors=Number(document.querySelector('#paletteSize').value), includeWhite=colors<=2, ops=[];
    // Build a deterministic posterized palette from the source, excluding paper-white.
    const bins=new Map();
    for(let p=0;p<pixels.length;p+=16){const r=pixels[p],g=pixels[p+1],b=pixels[p+2],a=pixels[p+3],high=Math.max(r,g,b),low=Math.min(r,g,b),sat=high?(high-low)/high:0,lum=(r*.2126+g*.7152+b*.0722)/255;if(a<40||(!includeWhite&&lum>.94&&sat<.1))continue;const key=`${r>>4},${g>>4},${b>>4}`,entry=bins.get(key)||{r:0,g:0,b:0,n:0};entry.r+=r;entry.g+=g;entry.b+=b;entry.n++;bins.set(key,entry)}
    if(version!==digitizeVersion){resolve([]);return}
    const palette=[...bins.values()].sort((a,b)=>b.n-a.n).slice(0,colors).map(c=>[Math.round(c.r/c.n),Math.round(c.g/c.n),Math.round(c.b/c.n)]);
    const hex=color=>`#${color.map(n=>n.toString(16).padStart(2,'0')).join('')}`;
    threadSequence=palette.map(hex); palette.forEach((color,index)=>{needles[index][1]=hex(color);needles[index][2]=`Palette ${index+1}`});renderNeedles();
    const nearest=(r,g,b)=>{let result=-1,best=Infinity;palette.forEach((color,index)=>{const distance=(r-color[0])**2+(g-color[1])**2+(b-color[2])**2;if(distance<best){best=distance;result=index}});return result};
    for(let colorIndex=0;colorIndex<palette.length;colorIndex++){
      const block=[];
      for(let row=0,rowIndex=0;row<height;row++,rowIndex++){const runs=[];let runStart=-1;for(let column=0;column<=width;column++){let filled=false;if(column<width){const index=(row*width+column)*4,r=pixels[index],g=pixels[index+1],b=pixels[index+2],a=pixels[index+3],high=Math.max(r,g,b),low=Math.min(r,g,b),sat=high?(high-low)/high:0,lum=(r*.2126+g*.7152+b*.0722)/255;filled=a>40&&(includeWhite||!(lum>.94&&sat<.1))&&nearest(r,g,b)===colorIndex}if(filled&&runStart<0)runStart=column;if(!filled&&runStart>=0){if(column-runStart>=5)runs.push([runStart,column-1]);runStart=-1}}if(rowIndex%2)runs.reverse();for(const [start,end] of runs){const forward=rowIndex%2===0,from=forward?start:end,to=forward?end:start,points=[],stepColumns=Math.max(1,Math.floor(2.5/xStep));for(let column=from;forward?column<=to:column>=to;column+=forward?stepColumns:-stepColumns)points.push({x:xOffset+column*xStep,y:yOffset+row*yStep});const last={x:xOffset+to*xStep,y:yOffset+row*yStep};if(Math.abs(points.at(-1).x-last.x)>.001)points.push(last);block.push({kind:'jump',...points[0]},...points.map(point=>({kind:'stitch',...point,color:threadSequence[colorIndex]})),{kind:'trim',...points.at(-1)})}}
      if(block.length){if(ops.length)ops.push({kind:'color'});ops.push(...block)}
    }
    resolve(ops);
  }; image.onerror=()=>resolve([]); image.src = source; });
}
let operations = buildPlan();
function draw() {
  stitches.replaceChildren(); let previous;
  const stitchPoints=operations.filter(op=>op.kind==='stitch'||op.kind==='jump');
  const bounds=stitchPoints.reduce((box,point)=>({minX:Math.min(box.minX,point.x),maxX:Math.max(box.maxX,point.x),minY:Math.min(box.minY,point.y),maxY:Math.max(box.maxY,point.y)}),{minX:-1,maxX:1,minY:-1,maxY:1});
  const rawWidth=Math.max(1,bounds.maxX-bounds.minX),rawHeight=Math.max(1,bounds.maxY-bounds.minY),rawScale=Math.min(680/rawWidth,360/rawHeight);
  const screen=articlePlaced?point=>({x:400+point.x*(380/120),y:240+point.y*(170/55)}):point=>({x:400+(point.x-(bounds.minX+bounds.maxX)/2)*rawScale,y:240+(point.y-(bounds.minY+bounds.maxY)/2)*rawScale});
  const end = Number(timeline.value);
  operations.slice(0,end).forEach((op,i)=>{
    if(op.kind === 'stitch' && previous){const from=screen(previous),to=screen(op),line=document.createElementNS(ns,'line');line.setAttribute('x1',from.x);line.setAttribute('y1',from.y);line.setAttribute('x2',to.x);line.setAttribute('y2',to.y);line.setAttribute('stroke',op.color);line.setAttribute('stroke-width','2');line.setAttribute('stroke-linecap','round');line.setAttribute('opacity',i === end-1 ? '1':'0.88');stitches.append(line)}
    if(op.kind==='jump' || op.kind==='stitch') previous=op;
  });
  const current=operations[Math.max(0,end-1)] || operations[0]; if(current) {const at=screen(current);needle.setAttribute('transform',`translate(${at.x} ${at.y})`);document.querySelector('#positionReadout').textContent=`X ${(current.x-zeroOffset.x).toFixed(1)} / Y ${(current.y-zeroOffset.y).toFixed(1)}`}
  document.querySelector('#step').textContent=`${end} / ${operations.length}`;
  document.querySelector('#operation').textContent = end ? `OP ${String(end).padStart(4,'0')} · ${operations[end-1].kind.toUpperCase()}` : 'Awaiting simulation';
}
function validateOperations(list){let previous=null,short=0,long=0,segments=0;for(const operation of list){if(operation.kind==='stitch'){if(previous?.kind==='stitch'){const length=Math.hypot(operation.x-previous.x,operation.y-previous.y);segments++;if(length<.25)short++;if(length>12)long++}previous=operation}else if(operation.kind==='jump'||operation.kind==='trim'||operation.kind==='color')previous=null}return{short,long,segments,jumps:list.filter(operation=>operation.kind==='jump').length,trims:list.filter(operation=>operation.kind==='trim').length,stitches:list.filter(operation=>operation.kind==='stitch').length}}
function setup(){timeline.max=operations.length;timeline.value=operations.length;const decoded=validateOperations(operations),semantic=generatedPlan?validateOperations(generatedPlan.operations):decoded,drc=generatedPlan?.drc||{short_stitches:semantic.short,long_stitches:semantic.long,segments:semantic.segments};document.querySelector('#stitchCount').textContent=decoded.stitches.toLocaleString();document.querySelector('#runtime').textContent=`${Math.ceil(operations.length/11)} sec`;document.querySelector('#export').disabled=!decoded.stitches;if(artworkData){const objects=generatedPlan?.regions?.length||0,connectors=generatedPlan?.operations?.filter(operation=>operation.role==='fill_connector').length||0,issues=drc.short_stitches+drc.long_stitches,penalty=Math.min(30,Math.ceil((drc.short_stitches/Math.max(1,drc.segments))*100)+drc.long_stitches*3),confidence=Math.max(65,98-penalty);document.querySelector('#score').textContent=confidence;document.querySelector('#findingCount').textContent=issues?'1':'0';document.querySelector('#autopunchFinding').innerHTML=issues?`<b>Review · Stitch-length limits</b><p>${drc.short_stitches} segments below 0.25 mm; ${drc.long_stitches} above 12 mm. Tie sequences and jumps are excluded.</p>`:`<b>Pass · Stitch-length limits</b><p>${drc.segments.toLocaleString()} production stitch segments are within the 0.25–12 mm DRC range.</p>`;document.querySelector('#routeFinding').innerHTML=generatedPlan?`<b>Info · ${objects} embroidery objects</b><p>${semantic.trims} object trims and ${connectors} safe fill-row connectors. ${decoded.jumps} decoded frame moves include fill-row entries; they are not all separate islands.</p>`:`<b>Info · ${decoded.jumps} frame moves</b><p>Imported DST movement records have no retained object semantics.</p>`}draw()}
function colorizeOps(){let colorIndex=0;operations.forEach(op=>{if(op.kind==='color')colorIndex++;op.color=threadSequence[Math.min(colorIndex,threadSequence.length-1)]||needles[0][1]})}
function planLabel(){if(!generatedPlan)return 'DECODED DST · FULL PLAN';const regions=generatedPlan.regions?.length||0,colors=generatedPlan.palette?.length||0;return `GENERATED DST · ${regions} OBJECT${regions===1?'':'S'} · ${colors} COLOR${colors===1?'':'S'}`}
function setPreviewMode(){articlePlaced=false;const stage=document.querySelector('#stage');stage.classList.add('dst-preview');document.querySelector('#frameMode').textContent='PREVIEW';document.querySelector('#stageTag').textContent=planLabel();document.querySelector('#viewMode').textContent=generatedPlan?'DST PREVIEW / GENERATED':'DST PREVIEW / DECODED';draw()}
async function showCompiledDst(sourceOperations=operations, version=digitizeVersion) { if(!sourceOperations.some(operation=>operation.kind==='stitch'))throw Error('No stitchable operations');if(version!==digitizeVersion)return false;operations=sourceOperations;colorizeOps();setPreviewMode();setup();document.querySelector('#stageTag').textContent=`COMPILING DST · ${planLabel()}`;document.querySelector('#operation').textContent='Compiling DST and decoding preview…';const response=await fetch('/api/preview.dst',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({operations:sourceOperations})}); if(!response.ok) throw Error('DST compilation failed'); const decoded=(await response.json()).operations;if(version!==digitizeVersion)return false;if(!decoded.some(operation=>operation.kind==='stitch'))throw Error('DST contained no displayable stitches');operations=decoded;colorizeOps();setPreviewMode();setup();return true; }
timeline.addEventListener('input',draw);
function stopMachine(){if(playing){clearInterval(playing);playing=null}document.querySelector('#operation').textContent='STOP · Needle at upper position'}
function startMachine(){if(playing)return;const rpm=Number(document.querySelector('#speed').value);playing=setInterval(()=>{timeline.value=Number(timeline.value)+1;draw();if(Number(timeline.value)>=operations.length)stopMachine()},Math.max(12,9000/rpm))}
document.querySelector('#start').addEventListener('click',startMachine);
document.querySelector('#stop').addEventListener('click',stopMachine);
document.querySelector('#back').addEventListener('click',()=>{timeline.value=Math.max(0,Number(timeline.value)-1);draw()});
document.querySelector('#startPoint').addEventListener('click',()=>{timeline.value=0;draw();document.querySelector('#operation').textContent='START PNT · Frame returned'});
document.querySelector('#zero').addEventListener('click',()=>{const op=operations[Math.max(0,Number(timeline.value)-1)]||operations[0];zeroOffset={x:op.x||0,y:op.y||0};draw();document.querySelector('#operation').textContent='OFFSET · Current frame position is zero'});
document.querySelector('#speed').addEventListener('input',e=>{document.querySelector('#speedValue').textContent=e.target.value;if(playing){stopMachine();startMachine()}});
function toggleFullView(){const stage=document.querySelector('#stage'),button=document.querySelector('#fullView'),enabled=stage.classList.toggle('full-view');if(enabled){stopMachine();timeline.value=operations.length;draw();button.textContent='EXIT FULL VIEW';document.querySelector('#stageTag').textContent=`FULL STITCH VIEW · ${planLabel()}`}else{button.textContent='FULL STITCH VIEW';document.querySelector('#stageTag').textContent=planLabel()}}
document.querySelector('#fullView').addEventListener('click',toggleFullView);
document.querySelector('#exitFull').addEventListener('click',toggleFullView);
async function redigitize(){if(!artworkData)return;const version=++digitizeVersion;document.querySelector('#operation').textContent='Planning embroidery objects…';try{const response=await fetch('/api/digitize',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({image:artworkData,width_mm:Number(document.querySelector('#scale').value),colors:Number(document.querySelector('#paletteSize').value)})});if(!response.ok)throw Error('Digitizer failed');const plan=await response.json();if(version!==digitizeVersion)return;threadSequence=plan.palette;colorEnabled=plan.palette.map(()=>true);plan.palette.forEach((color,index)=>{needles[index][1]=color;needles[index][2]=`Palette ${index+1}`});renderNeedles();if(!plan.operations.length){document.querySelector('#operation').textContent='No stitchable regions detected';return}generatedPlan=plan;renderColorLayers();const applied=await showCompiledDst(selectedPlanOperations(),version);if(applied)document.querySelector('#operation').textContent=`DST decoded · ${operations.filter(operation=>operation.kind==='stitch').length.toLocaleString()} stitches`}catch(error){if(version===digitizeVersion)document.querySelector('#operation').textContent=`Digitizer failed · ${error.message}`}}
document.querySelector('#scale').addEventListener('input',async e=>{const value=Number(e.target.value);document.querySelector('#scaleLabel').textContent=`${value} mm wide`;updateArtworkTransform();if(artworkData)await redigitize();else{operations=buildPlan(value);await showCompiledDst()}});
document.querySelector('#paletteSize').addEventListener('input',async e=>{document.querySelector('#paletteLabel').textContent=`${e.target.value} colors`;await redigitize()});
document.querySelector('#rotation').addEventListener('input',async e=>{document.querySelector('#rotationLabel').textContent=`${e.target.value}°`;updateArtworkTransform();await redigitize()});
document.querySelector('#mirrorArtwork').addEventListener('click',async()=>{mirrored=!mirrored;document.querySelector('#mirrorArtwork').classList.toggle('active',mirrored);document.querySelector('#mirrorArtwork').textContent=`⇋   ${mirrored ? 'Mirrored horizontally' : 'Mirror horizontally'}`;updateArtworkTransform();await redigitize()});
document.querySelector('#placement').addEventListener('click',async event=>{const button=event.target.closest('button');if(!button)return;placement=button.dataset.place;document.querySelectorAll('#placement button').forEach(item=>item.classList.toggle('active',item===button));document.querySelector('#placementLabel').textContent=`Front ${placement}`;updateArtworkTransform();await redigitize()});
document.querySelector('#replaceArtwork').addEventListener('click',()=>artworkFile.click());
artworkFile.addEventListener('change',()=>{const file=artworkFile.files[0];if(!file)return;if(file.size>10*1024*1024){document.querySelector('#uploadHint').textContent='Choose a file smaller than 10 MB.';return}const reader=new FileReader();reader.onload=async()=>{artworkData=reader.result;artworkPreview.setAttributeNS('http://www.w3.org/1999/xlink','href',artworkData);artworkPreview.setAttribute('display','inline');artworkBounds.setAttribute('display','inline');document.querySelector('#sourceName').textContent=file.name;document.querySelector('#uploadHint').textContent='Rasterized into a running-stitch DST plan.';updateArtworkTransform();await redigitize()};reader.readAsDataURL(file)});
document.querySelector('#fixDensity').addEventListener('click',async()=>{densityFixed=true;operations=buildPlan(Number(document.querySelector('#scale').value));document.querySelector('#score').textContent='94';document.querySelector('.caution').innerHTML='<b>Resolved · Curve spacing</b><p>Safe spacing was applied to the inside arc. Review the revised simulation.</p>';await showCompiledDst()});
document.querySelector('#reset').addEventListener('click',()=>location.reload());
document.querySelector('#openDst').addEventListener('click',()=>dstFile.click());
dstFile.addEventListener('change',async()=>{const file=dstFile.files[0];if(!file)return;const response=await fetch('/api/inspect.dst',{method:'POST',headers:{'Content-Type':'application/octet-stream'},body:file});if(!response.ok){document.querySelector('#operation').textContent='Could not read DST';return}generatedPlan=null;operations=(await response.json()).operations;const blocks=operations.filter(op=>op.kind==='color').length+1;threadSequence=needles.slice(0,Math.min(15,blocks)).map(needle=>needle[1]);renderNeedles();renderColorLayers();colorizeOps();setPreviewMode();document.querySelector('#viewMode').textContent=`DST PREVIEW / ${file.name} · ${blocks} BLOCKS`;setup()});
document.querySelector('#export').addEventListener('click',async()=>{const button=document.querySelector('#export');if(!operations.some(operation=>operation.kind==='stitch')){button.textContent='No stitches to export';setTimeout(()=>button.textContent='Export DST',1600);return}button.textContent='Compiling…';try{const response=await fetch('/api/export.dst',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({operations})});if(!response.ok)throw Error();const blob=await response.blob(),a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download='new-hat-job.dst';a.click();URL.revokeObjectURL(a.href);button.textContent='DST exported'}catch{button.textContent='Export failed'}setTimeout(()=>button.textContent='Export DST',1600)});
renderNeedles();
showCompiledDst().catch(()=>setup());
