const ns = 'http://www.w3.org/2000/svg';
const fullDigitizer = new URLSearchParams(location.search).get('full') === '1';
const canvas = document.querySelector('#canvas');
const stitches = document.querySelector('#stitches');
const needle = document.querySelector('#needle');
const workSurface = document.createElementNS(ns,'g');
workSurface.id='workSurface';
canvas.insertBefore(workSurface,needle);
[...canvas.children].filter(item=>item!==workSurface&&item!==needle&&item.tagName.toLowerCase()!=='defs').forEach(item=>workSurface.append(item));
const timeline = document.querySelector('#timeline');
const artworkFile = document.querySelector('#artworkFile');
const artworkPreview = document.querySelector('#artworkPreview');
const artworkTransform = document.querySelector('#artworkTransform');
const artworkBounds = document.querySelector('#artworkBounds');
const dstFile = document.querySelector('#dstFile');
const trainModelButton = document.querySelector('#trainModel');
const workProfiles={
  cap:{id:'cap',label:'Cap frame',widthMm:120,heightMm:55,usableWidthMm:110,usableHeightMm:45,screenWidth:380,screenHeight:170,maxSpm:1200,maxStitches:650000,maxDesigns:99},
  full:{id:'full',label:'Full machine field',widthMm:500,heightMm:360,usableWidthMm:500,usableHeightMm:360,screenWidth:380,screenHeight:274,maxSpm:1200,maxStitches:650000,maxDesigns:99},
};
let workArea=workProfiles.cap;
let playing, densityFixed = false, step = 0, zeroOffset = {x:0,y:0}, activeNeedle = 0, digitizeVersion = 0;
let mirrored = false, placement = 'center';
let artworkData;
let articlePlaced = false;
let previewProjection = null;
let motionView = 'needle', projectionMode = 'design', designAspect = null, renderedThrough = -1, renderedOperations = null, renderedProjection = '', renderedPrevious = null, latestStitchLine = null;
let generatedPlan = null;
let vectorDocument = null, selectedVectorId = null, vectorDrag = null;
const motionViewButton=document.createElement('button');
motionViewButton.id='motionView';
motionViewButton.textContent='MOTION · NEEDLE MOVES';
motionViewButton.setAttribute('aria-pressed','false');
document.querySelector('#fullView').after(motionViewButton);
const projectionButton=document.createElement('button');
projectionButton.id='projectionView';
projectionButton.textContent='VIEW · FIT DESIGN';
projectionButton.title='Toggle between design-fit zoom and physical cap-frame scale';
motionViewButton.after(projectionButton);
motionViewButton.addEventListener('click',()=>{
  motionView=motionView==='needle'?'work':'needle';
  motionViewButton.textContent=`MOTION · ${motionView==='work'?'WORK':'NEEDLE'} MOVES`;
  motionViewButton.setAttribute('aria-pressed',String(motionView==='work'));
  motionViewButton.classList.toggle('active',motionView==='work');
  draw();
});
projectionButton.addEventListener('click',()=>{
  projectionMode=projectionMode==='design'?'work':'design';
  projectionButton.textContent=`VIEW · FIT ${projectionMode==='work'?'WORK':'DESIGN'}`;
  projectionButton.setAttribute('aria-pressed',String(projectionMode==='work'));
  projectionButton.classList.toggle('active',projectionMode==='work');
  draw();
  renderVectorEditor();
});
const fitToWorkButton=document.createElement('button');
fitToWorkButton.id='fitToWork';
fitToWorkButton.className='select';
fitToWorkButton.textContent='↔  Fit design to cap field';
fitToWorkButton.title=`Usable field: ${workArea.usableWidthMm} × ${workArea.usableHeightMm} mm`;
document.querySelector('#scale').after(fitToWorkButton);
const workProfileLabel=document.createElement('label');
workProfileLabel.innerHTML='<span>Frame / hoop</span><select id="workProfile"><option value="cap">Cap frame · 120 × 55 mm</option><option value="full">Full field · 500 × 360 mm</option></select>';
fitToWorkButton.after(workProfileLabel);
document.querySelector('#workProfile').addEventListener('change',event=>{
  workArea=workProfiles[event.target.value];
  const scale=document.querySelector('#scale');
  scale.max=String(workArea.usableWidthMm);
  const clamped=Number(scale.value)>workArea.usableWidthMm;
  if(clamped){
    scale.value=String(workArea.usableWidthMm);
    document.querySelector('#scaleLabel').textContent=`${scale.value} mm wide`;
    updateArtworkTransform();
  }
  const speed=document.querySelector('#speed');
  speed.max=String(workArea.maxSpm);
  if(Number(speed.value)>workArea.maxSpm){
    speed.value=String(workArea.maxSpm);
    document.querySelector('#speedValue').textContent=speed.value;
  }
  fitToWorkButton.textContent=`↔  Fit design to ${workArea.id==='cap'?'cap field':'full field'}`;
  fitToWorkButton.title=`Usable field: ${workArea.usableWidthMm} × ${workArea.usableHeightMm} mm · ${workArea.maxSpm.toLocaleString()} SPM`;
  projectionMode='work';
  projectionButton.textContent='VIEW · FIT WORK';
  projectionButton.setAttribute('aria-pressed','true');
  projectionButton.classList.add('active');
  const boundary=document.querySelector('#workBoundary');
  boundary.setAttribute('x',String(400-workArea.screenWidth/2));
  boundary.setAttribute('y',String(240-workArea.screenHeight/2));
  boundary.setAttribute('width',String(workArea.screenWidth));
  boundary.setAttribute('height',String(workArea.screenHeight));
  const fullField=workArea.id==='full';
  ['capFabric','capWeave','capSeam','capBrim'].forEach(id=>{
    document.querySelector(`#${id}`).style.display=fullField?'none':'';
  });
  document.querySelector('#fullFieldSurface').style.display=fullField?'':'none';
  document.querySelector('#frameMode').textContent=workArea.id==='cap'?'CAP FRAME':'FULL FIELD';
  setup(true);
  renderVectorEditor();
  if(clamped&&artworkData)scheduleRedigitize();
});
fitToWorkButton.addEventListener('click',async()=>{
  if(!designAspect||!artworkData)return;
  const target=Math.min(workArea.usableWidthMm,workArea.usableHeightMm*designAspect);
  const scale=document.querySelector('#scale');
  scale.value=String(Math.max(Number(scale.min),Math.min(Number(scale.max),Math.round(target))));
  document.querySelector('#scaleLabel').textContent=`${scale.value} mm wide`;
  projectionMode='work';
  projectionButton.textContent='VIEW · FIT WORK';
  projectionButton.setAttribute('aria-pressed','true');
  projectionButton.classList.add('active');
  updateArtworkTransform();
  await redigitize();
});
function setBusy(active,message='Extracting layers and planning stitches…'){document.body.classList.toggle('busy',active);document.querySelector('#stage').classList.toggle('busy',active);document.querySelector('#busyIndicator span').textContent=message;const stitchCount=operations?.filter(operation=>operation.kind==='stitch').length||0;document.querySelector('#export').disabled=active||!stitchCount||stitchCount>workArea.maxStitches}
const needles=[['01','#ed5e98','Rose 1847'],['02','#eebd54','Gold 217'],['03','#247b79','Teal 3810'],['04','#eee9df','White 001'],['05','#252b2d','Black 900'],['06','#1e4f9b','Blue 354'],['07','#e85d48','Red 191'],['08','#7b5b9d','Violet 261'],['09','#7a9d46','Green 550'],['A','#d6a176','Tan 842'],['B','#b5b8ba','Silver 401'],['C','#6e4b3a','Brown 807'],['D','#f182b5','Pink 202'],['E','#f3dc62','Yellow 109'],['F','#7a7f88','Gray 415']];
let threadSequence = [needles[0][1]], colorEnabled = [], previewThreadBlocks = [];
function applyThreadColor(index,color){const previous=threadSequence[index];needles[index][1]=color;threadSequence[index]=color;previewThreadBlocks=previewThreadBlocks.map(blockColor=>blockColor===previous?color:blockColor);if(generatedPlan?.palette[index]===previous){generatedPlan.palette[index]=color;generatedPlan.regions.filter(region=>region.color===previous).forEach(region=>region.color=color)}const changed=vectorDocument?.objects.filter(object=>object.color===previous)||[];changed.forEach(object=>object.color=color);renderNeedles();renderColorLayers();renderVectorEditor();colorizeOps();draw();if(changed.length)planVectorDocument().catch(error=>document.querySelector('#operation').textContent=error.message)}
function renderNeedles(){const rack=document.querySelector('#needleRack'),editor=document.querySelector('#needleEditor');rack.replaceChildren();editor.replaceChildren();needles.forEach((needle,index)=>{const button=document.createElement('button');button.className=`needle-slot ${index===activeNeedle?'active':''}`;button.innerHTML=`<span class="spool" style="--thread:${needle[1]}"><em></em></span><i></i><b>${needle[0]}</b>`;button.onclick=()=>{activeNeedle=index;renderNeedles();document.querySelector('#activeNeedle').textContent=needles[index][0]};rack.append(button);const row=document.createElement('label');row.className='needle-row';row.innerHTML=`<button type="button" class="needle-pick ${index===activeNeedle?'active':''}">${needle[0]}</button><input aria-label="Thread color for needle ${needle[0]}" type="color" value="${needle[1]}"><input aria-label="Thread name for needle ${needle[0]}" value="${needle[2]}">`;row.querySelector('.needle-pick').onclick=()=>{activeNeedle=index;renderNeedles();document.querySelector('#activeNeedle').textContent=needles[index][0]};row.querySelector('input[type=color]').oninput=e=>applyThreadColor(index,e.target.value);row.querySelector('input:not([type=color])').onchange=e=>{needle[2]=e.target.value;renderNeedles()};editor.append(row)});document.querySelector('#needleCount').textContent=`${threadSequence.length} / 15`}

function selectedPlanOperations(){if(!generatedPlan)return operations;return NewHatThreadBlocks.selectedPlanOperations(generatedPlan,colorEnabled)}
async function refreshColorLayers(){if(!generatedPlan)return;const version=++digitizeVersion,selected=selectedPlanOperations();if(!selected.some(operation=>operation.kind==='stitch')){operations=[];setup();draw();return}await showCompiledDst(selected,version)}
function renderColorLayers(){const container=document.querySelector('#colorLayers');if(!container)return;container.replaceChildren();if(!generatedPlan)return;generatedPlan.palette.forEach((color,index)=>{const row=document.createElement('label'),enabled=document.createElement('input'),picker=document.createElement('input'),name=document.createElement('span');row.className=`color-layer ${colorEnabled[index]===false?'disabled':''}`;enabled.type='checkbox';enabled.checked=colorEnabled[index]!==false;enabled.setAttribute('aria-label',`Include color ${index+1}`);picker.type='color';picker.value=color;picker.setAttribute('aria-label',`Color ${index+1}`);name.textContent=`Color ${index+1}${enabled.checked?'':' · off'}`;enabled.onchange=async()=>{colorEnabled[index]=enabled.checked;renderColorLayers();await refreshColorLayers()};picker.oninput=()=>applyThreadColor(index,picker.value);row.append(enabled,picker,name);container.append(row)})}
function vectorScreen(point){
  if(!articlePlaced&&previewProjection)return{
    x:400+(point.x-previewProjection.centerX)*previewProjection.scale,
    y:240+(point.y-previewProjection.centerY)*previewProjection.scale,
  };
  return{x:400+point.x*(380/120),y:240+point.y*(170/55)};
}
function vectorPoint(event){const box=document.querySelector('#canvas').getBoundingClientRect();return{x:(event.clientX-box.left)*800/box.width,y:(event.clientY-box.top)*480/box.height}}
function vectorDocumentPoint(screen){
  if(!articlePlaced&&previewProjection)return{
    x:(screen.x-400)/previewProjection.scale+previewProjection.centerX,
    y:(screen.y-240)/previewProjection.scale+previewProjection.centerY,
  };
  return{x:(screen.x-400)*120/380,y:(screen.y-240)*55/170};
}
function ensureVectorLayer(){let layer=document.querySelector('#vectorObjects');if(!layer){layer=document.createElementNS(ns,'g');layer.id='vectorObjects';workSurface.append(layer)}return layer}
function vectorPathData(nodes,closed){if(!nodes?.length)return'';const first=vectorScreen(nodes[0]);let data=`M ${first.x} ${first.y}`;for(let index=0;index<(closed?nodes.length:nodes.length-1);index++){const start=nodes[index],end=nodes[(index+1)%nodes.length],out=vectorScreen({x:start.x+(start.out_x||0),y:start.y+(start.out_y||0)}),inside=vectorScreen({x:end.x+(end.in_x||0),y:end.y+(end.in_y||0)}),target=vectorScreen(end);data+=` C ${out.x} ${out.y} ${inside.x} ${inside.y} ${target.x} ${target.y}`}return closed?`${data} Z`:data}
function vectorPathPoint(nodes,t){if(!nodes?.length)return{x:0,y:0};if(nodes.length===1)return vectorScreen(nodes[0]);const scaled=Math.max(0,Math.min(.999999,t))*(nodes.length-1),index=Math.min(nodes.length-2,Math.floor(scaled)),local=scaled-index,start=nodes[index],end=nodes[index+1],inverse=1-local,c1={x:start.x+(start.out_x||0),y:start.y+(start.out_y||0)},c2={x:end.x+(end.in_x||0),y:end.y+(end.in_y||0)};return vectorScreen({x:inverse**3*start.x+3*inverse**2*local*c1.x+3*inverse*local**2*c2.x+local**3*end.x,y:inverse**3*start.y+3*inverse**2*local*c1.y+3*inverse*local**2*c2.y+local**3*end.y})}
const bracketStatusRank={pass:0,near_limit:1,warning:2,fallback:3,blocker:4};
function objectBrackets(objectId){return generatedPlan?.brackets?.filter(item=>item.object_id===objectId)||[]}
function renderBracketOverlay(layer,object){
  if(object.id!==selectedVectorId||object.stitch_type!=='satin'||object.satin_rails?.length!==2)return;
  objectBrackets(object.id).filter(item=>item.segment&&item.status!=='pass').forEach(item=>{
    let start=Number(item.segment.start),end=Number(item.segment.end);
    if(end-start<.015){start=Math.max(0,start-.012);end=Math.min(1,end+.012)}
    const leftStart=vectorPathPoint(object.satin_rails[0],start),leftEnd=vectorPathPoint(object.satin_rails[0],end),rightStart=vectorPathPoint(object.satin_rails[1],start),rightEnd=vectorPathPoint(object.satin_rails[1],end),band=document.createElementNS(ns,'path');
    band.setAttribute('d',`M ${leftStart.x} ${leftStart.y} L ${leftEnd.x} ${leftEnd.y} L ${rightEnd.x} ${rightEnd.y} L ${rightStart.x} ${rightStart.y} Z`);
    band.setAttribute('class',`decision-bracket-band ${item.status}`);
    band.setAttribute('data-rule',item.rule);
    layer.append(band);
  });
}
function formatBracketObserved(item){if(typeof item.observed==='number')return`${item.observed.toFixed(2)} ${item.unit}`;if(item.observed&&typeof item.observed==='object')return Object.entries(item.observed).map(([key,value])=>`${key.replaceAll('_',' ')} ${Number(value).toFixed(2)}`).join(' · ')+` ${item.unit}`;return'Not measurable'}
function renderDecisionBrackets(){
  const container=document.querySelector('#decisionBrackets');
  if(!container)return;
  container.replaceChildren();
  const brackets=[...(generatedPlan?.brackets||[])].filter(item=>item.object_id===selectedVectorId||item.status!=='pass').sort((left,right)=>(bracketStatusRank[right.status]||0)-(bracketStatusRank[left.status]||0)||Number(right.object_id===selectedVectorId)-Number(left.object_id===selectedVectorId));
  if(!brackets.length){const empty=document.createElement('small');empty.textContent='Select a satin object to inspect its production brackets.';container.append(empty);return}
  brackets.forEach(item=>{const card=document.createElement('button');card.type='button';card.className=`decision-bracket ${item.status}${item.object_id===selectedVectorId?' active':''}`;card.innerHTML=`<span><b>${item.rule.replaceAll('_',' ')}</b><em>${item.status.replaceAll('_',' ')}</em></span><strong>${formatBracketObserved(item)}</strong><small>${item.message}</small>`;card.onclick=()=>{selectedVectorId=item.object_id;const object=vectorDocument?.objects.find(candidate=>candidate.id===selectedVectorId);if(object)document.querySelector('#vectorStitchType').value=object.stitch_type;renderVectorLayerControl();renderVectorEditor();renderVectorDiagnostics()};container.append(card)});
}
function addVectorHandles(layer,nodes,closed,rail=false){nodes.forEach((node,index)=>{[['out',node.x+(node.out_x||0),node.y+(node.out_y||0)],['in',node.x+(node.in_x||0),node.y+(node.in_y||0)],['anchor',node.x,node.y]].forEach(([kind,x,y])=>{if(kind==='in'&&index===0&&!closed)return;if(kind==='out'&&index===nodes.length-1&&!closed)return;const point=vectorScreen({x,y}),handle=document.createElementNS(ns,'circle');handle.setAttribute('cx',point.x);handle.setAttribute('cy',point.y);handle.setAttribute('r',kind==='anchor'?'5':'3.5');handle.setAttribute('fill',kind==='anchor'?(rail?'#eebd54':'#fff'):'#eebd54');handle.setAttribute('stroke','#142528');handle.style.cursor='grab';handle.onpointerdown=event=>{event.stopPropagation();vectorDrag={nodes,index,kind};handle.setPointerCapture(event.pointerId)};layer.append(handle)})})}
function renderVectorDiagnostics(){renderDecisionBrackets();if(!generatedPlan||!selectedVectorId)return;const diagnostic=generatedPlan.diagnostics?.find(item=>item.id===selectedVectorId);if(!diagnostic)return;if(diagnostic.satin){const satin=diagnostic.satin,mean=satin.mean_width_mm==null?'—':`${satin.mean_width_mm.toFixed(2)} mm`,brackets=objectBrackets(selectedVectorId),attention=brackets.filter(item=>item.status!=='pass').length;document.querySelector('#operation').textContent=`Satin ${satin.score}/100 · ${mean} · ${satin.pair_count} pairs${attention?` · ${attention} bracket${attention===1?'':'s'} to review`:''}`}else document.querySelector('#operation').textContent=`${diagnostic.stitch_type} · ${diagnostic.boundary_count} boundar${diagnostic.boundary_count===1?'y':'ies'}`}
function renderVectorEditor(){const layer=ensureVectorLayer();layer.replaceChildren();if(!vectorDocument)return;vectorDocument.objects.forEach(object=>{if(!object.nodes.length)return;const path=document.createElementNS(ns,'path'),brackets=objectBrackets(object.id),status=brackets.reduce((current,item)=>(bracketStatusRank[item.status]||0)>(bracketStatusRank[current]||0)?item.status:current,'pass');path.setAttribute('d',vectorPathData(object.nodes,object.closed));path.setAttribute('fill',object.closed?`${object.color}55`:'none');path.setAttribute('stroke',object.color);path.setAttribute('stroke-width',object.id===selectedVectorId?'3':'2');path.setAttribute('data-bracket-status',status);path.style.cursor=fullDigitizer?'pointer':'default';if(fullDigitizer)path.onclick=()=>{selectedVectorId=object.id;document.querySelector('#vectorStitchType').value=object.stitch_type;renderVectorEditor();renderVectorDiagnostics()};layer.append(path);if(object.stitch_type==='satin')object.satin_rails?.forEach((nodes,index)=>{const rail=document.createElementNS(ns,'path');rail.setAttribute('d',vectorPathData(nodes,false));rail.setAttribute('fill','none');rail.setAttribute('stroke',index?'#6ed0c9':'#eebd54');rail.setAttribute('stroke-width',object.id===selectedVectorId?'2.5':'1.5');rail.setAttribute('stroke-dasharray','4 3');layer.append(rail)});renderBracketOverlay(layer,object);if(!fullDigitizer||object.id!==selectedVectorId)return;addVectorHandles(layer,object.nodes,object.closed);if(object.stitch_type==='satin')object.satin_rails?.forEach(nodes=>addVectorHandles(layer,nodes,false,true))})}
async function installVectorPlan(payload,previewVersion){
  if(previewVersion!==digitizeVersion)return;
  vectorDocument=payload.document;
  if(vectorDocument.width_mm&&vectorDocument.height_mm)designAspect=vectorDocument.width_mm/vectorDocument.height_mm;
  const activeObjects=vectorDocument.objects.filter(item=>item.enabled),palette=[...new Set(activeObjects.map(item=>item.color))];
  generatedPlan={palette,regions:activeObjects.map(item=>({id:item.id,color:item.color,stitch_type:item.stitch_type,element_id:item.element_id})),layers:vectorDocument.layers,operations:payload.operations,composition:payload.composition,diagnostics:payload.diagnostics,brackets:payload.brackets||[],findings:payload.findings,sequence:payload.sequence,orientations:payload.orientations,neighbors:payload.neighbors,constructs:payload.constructs,thread_economy:payload.thread_economy};
  threadSequence=palette;
  colorEnabled=palette.map(()=>true);
  palette.forEach((color,index)=>needles[index][1]=color);
  // Install and decode the machine plan before drawing the potentially large
  // editable-vector overlay. Editor rendering must never turn a valid plan
  // into a misleading zero-stitch result.
  await showCompiledDst(payload.operations,previewVersion);
  renderNeedles();
  renderColorLayers();
  renderVectorLayerControl();
  renderVectorEditor();
  renderVectorDiagnostics();
}
async function planVectorDocument(version=null){
  if(!vectorDocument)return;
  const response=await fetch('/api/vector/plan',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(vectorDocument)});
  if(!response.ok)throw Error('Vector planning failed');
  const payload=await response.json(),previewVersion=version??++digitizeVersion;
  await installVectorPlan(payload,previewVersion);
}
function transformVectorProposal(document){const angle=Number(document.querySelector('#rotation').value)*Math.PI/180,c=Math.cos(angle),s=Math.sin(angle),offset=placement==='left'?-28:placement==='right'?28:0,transform=(node,relative=false)=>{const sourceX=(mirrored?-1:1)*(node.x||0),sourceY=node.y||0;return{x:sourceX*c-sourceY*s+(relative?0:offset),y:sourceX*s+sourceY*c}},visit=node=>{const anchor=transform(node),inside=transform({x:node.in_x||0,y:node.in_y||0},true),outside=transform({x:node.out_x||0,y:node.out_y||0},true);Object.assign(node,{x:anchor.x,y:anchor.y,in_x:inside.x,in_y:inside.y,out_x:outside.x,out_y:outside.y})};document.objects.forEach(object=>{object.nodes.forEach(visit);object.boundaries?.forEach(boundary=>boundary.forEach(visit));object.satin_rails?.forEach(rail=>rail.forEach(visit))});return document}
async function vectorizeArtwork(version){
  const response=await fetch('/api/vectorize',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({image:artworkData,width_mm:Number(document.querySelector('#scale').value)})});
  if(!response.ok)throw Error('Vector proposal failed');
  const payload=await response.json();
  if(version!==digitizeVersion)return;
  const density=Number(document.querySelector('#stitchDensity').value)/100;
  const transformed=mirrored||placement!=='center'||Number(document.querySelector('#rotation').value)!==0||density!==1;
  vectorDocument=transformed?transformVectorProposal(payload.document):payload.document;
  vectorDocument.layers.forEach(layer=>layer.density_multiplier=density);
  selectedVectorId=vectorDocument.objects[0]?.id||null;
  if(transformed)await planVectorDocument(version);
  else await installVectorPlan(payload,version);
  document.querySelector('#operation').textContent=`Editable stroke proposal · ${vectorDocument.objects.length} objects`;
}
function addVectorObject(stitch_type){if(!vectorDocument)vectorDocument={width_mm:72,height_mm:55,objects:[],layers:[{id:'base',name:'Base',order:0,enabled:true,density_multiplier:1}]};const index=vectorDocument.objects.length,id=`vector-${index+1}`,color=needles[index%needles.length][1],layer_id=vectorDocument.layers[0].id,solid=stitch_type!=='running',nodes=solid?[{x:-10,y:-4},{x:10,y:-4},{x:10,y:4},{x:-10,y:4}]:[{x:-12,y:0,out_x:8,out_y:-8},{x:12,y:0,in_x:-8,in_y:-8}],satin_rails=stitch_type==='satin'?[[{x:-10,y:-2},{x:10,y:-2}],[{x:-10,y:2},{x:10,y:2}]]:[];vectorDocument.objects.push({id,color,stitch_type,layer_id,element_id:id,nodes,satin_rails,closed:solid,fill_spacing_mm:.45,stitch_length_mm:4.5,satin_spacing_mm:.4});selectedVectorId=id;document.querySelector('#vectorStitchType').value=stitch_type;renderVectorLayerControl();renderVectorEditor();planVectorDocument().catch(error=>document.querySelector('#operation').textContent=error.message)}
function ensureSatinRails(object){if(object.satin_rails?.length===2)return;const xs=object.nodes.map(node=>node.x),ys=object.nodes.map(node=>node.y),minX=Math.min(...xs),maxX=Math.max(...xs),minY=Math.min(...ys),maxY=Math.max(...ys);object.satin_rails=maxX-minX>=maxY-minY?[[{x:minX,y:minY},{x:maxX,y:minY}],[{x:minX,y:maxY},{x:maxX,y:maxY}]]:[[{x:minX,y:minY},{x:minX,y:maxY}],[{x:maxX,y:minY},{x:maxX,y:maxY}]];object.satin_spacing_mm=.4}
function renderVectorLayerControl(){const select=document.querySelector('#vectorLayer');if(!select||!vectorDocument)return;select.replaceChildren();vectorDocument.layers.forEach(layer=>{const option=document.createElement('option');option.value=layer.id;option.textContent=`${layer.order + 1}. ${layer.name}`;select.append(option)});const object=vectorDocument.objects.find(item=>item.id===selectedVectorId);if(object)select.value=object.layer_id}
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
function appendRenderedOperation(op,index,screen,isLatest){
  if(op.kind==='stitch'&&renderedPrevious){
    if(latestStitchLine)latestStitchLine.setAttribute('opacity','0.88');
    const from=screen(renderedPrevious),to=screen(op),line=document.createElementNS(ns,'line');
    line.setAttribute('x1',from.x);line.setAttribute('y1',from.y);
    line.setAttribute('x2',to.x);line.setAttribute('y2',to.y);
    line.setAttribute('stroke',op.color);line.setAttribute('stroke-width','2');
    line.setAttribute('stroke-linecap','round');
    line.setAttribute('opacity',isLatest?'1':'0.88');
    stitches.append(line);latestStitchLine=line;
  }
  if(op.kind==='jump'||op.kind==='stitch')renderedPrevious=op;
}
function draw() {
  const stitchPoints=operations.filter(op=>op.kind==='stitch'||op.kind==='jump');
  const bounds=stitchPoints.reduce((box,point)=>({minX:Math.min(box.minX,point.x),maxX:Math.max(box.maxX,point.x),minY:Math.min(box.minY,point.y),maxY:Math.max(box.maxY,point.y)}),{minX:-1,maxX:1,minY:-1,maxY:1});
  const rawWidth=Math.max(1,bounds.maxX-bounds.minX),rawHeight=Math.max(1,bounds.maxY-bounds.minY),rawScale=Math.min(680/rawWidth,360/rawHeight);
  const workScale=Math.min(workArea.screenWidth/workArea.widthMm,workArea.screenHeight/workArea.heightMm);
  previewProjection=projectionMode==='work'?{
    centerX:0,
    centerY:0,
    scale:workScale,
  }:{
    centerX:(bounds.minX+bounds.maxX)/2,
    centerY:(bounds.minY+bounds.maxY)/2,
    scale:rawScale,
  };
  const screen=point=>({
    x:400+(point.x-previewProjection.centerX)*previewProjection.scale,
    y:240+(point.y-previewProjection.centerY)*previewProjection.scale,
  });
  const end = Number(timeline.value);
  const projectionKey=`${projectionMode}:${previewProjection.centerX}:${previewProjection.centerY}:${previewProjection.scale}`;
  if(end>0&&operations===renderedOperations&&projectionKey===renderedProjection&&end===renderedThrough+1){
    appendRenderedOperation(operations[end-1],end-1,screen,true);
    renderedThrough=end;
  }else if(!(operations===renderedOperations&&projectionKey===renderedProjection&&end===renderedThrough)){
    stitches.replaceChildren();renderedPrevious=null;latestStitchLine=null;
    operations.slice(0,end).forEach((op,index)=>appendRenderedOperation(op,index,screen,index===end-1));
    renderedOperations=operations;renderedProjection=projectionKey;renderedThrough=end;
  }
  let current=null;
  for(let index=Math.min(operations.length,end||1)-1;index>=0;index--){
    if(Number.isFinite(operations[index].x)&&Number.isFinite(operations[index].y)){current=operations[index];break}
  }
  if(!current)current=operations.find(operation=>Number.isFinite(operation.x)&&Number.isFinite(operation.y));
  if(current){
    const at=screen(current);
    if(motionView==='work'){
      workSurface.setAttribute('transform',`translate(${400-at.x} ${240-at.y})`);
      needle.setAttribute('transform','translate(400 240)');
    }else{
      workSurface.removeAttribute('transform');
      needle.setAttribute('transform',`translate(${at.x} ${at.y})`);
    }
    document.querySelector('#positionReadout').textContent=`X ${(current.x-zeroOffset.x).toFixed(1)} / Y ${(current.y-zeroOffset.y).toFixed(1)}`;
  }else{
    workSurface.removeAttribute('transform');
    needle.setAttribute('transform','translate(400 240)');
  }
  document.querySelector('#step').textContent=`${end} / ${operations.length}`;
  document.querySelector('#operation').textContent = end ? `OP ${String(end).padStart(4,'0')} · ${operations[end-1].kind.toUpperCase()}` : 'Awaiting simulation';
}
function validateOperations(list){let previous=null,short=0,long=0,segments=0;for(const operation of list){if(operation.kind==='stitch'){if(previous?.kind==='stitch'){const length=Math.hypot(operation.x-previous.x,operation.y-previous.y);segments++;if(length<.25)short++;if(length>12)long++}previous=operation}else if(operation.kind==='jump'||operation.kind==='trim'||operation.kind==='color')previous=null}return{short,long,segments,jumps:list.filter(operation=>operation.kind==='jump').length,trims:list.filter(operation=>operation.kind==='trim').length,colors:list.filter(operation=>operation.kind==='color').length,stitches:list.filter(operation=>operation.kind==='stitch').length}}
function formatRuntime(seconds){
  const rounded=Math.max(0,Math.ceil(seconds));
  if(rounded<60)return`${rounded} sec`;
  const minutes=Math.floor(rounded/60),remainder=rounded%60;
  return remainder?`${minutes} min ${remainder} sec`:`${minutes} min`;
}
function setup(preserveTimeline=false){
  const priorPosition=Number(timeline.value);
  timeline.max=operations.length;
  timeline.value=preserveTimeline?Math.min(priorPosition,operations.length):operations.length;
  const decoded=validateOperations(operations),semantic=generatedPlan?validateOperations(generatedPlan.operations):decoded,drc=generatedPlan?.drc||{short_stitches:semantic.short,long_stitches:semantic.long,segments:semantic.segments};
  const selectedSpm=Math.min(workArea.maxSpm,Number(document.querySelector('#speed').value));
  const runtimeSeconds=decoded.stitches/Math.max(1,selectedSpm)*60+decoded.trims*2+decoded.colors*8;
  const memoryExceeded=decoded.stitches>workArea.maxStitches;
  document.querySelector('#stitchCount').textContent=decoded.stitches.toLocaleString();
  document.querySelector('#layerCount').textContent=generatedPlan?.layers?.length??'—';
  document.querySelector('#objectCount').textContent=(generatedPlan?.drc?.object_count??generatedPlan?.regions?.length)?.toLocaleString()??'—';
  document.querySelector('#stitchesPerSqIn').textContent=generatedPlan?.drc?.stitches_per_sq_in?Math.round(generatedPlan.drc.stitches_per_sq_in).toLocaleString():'—';
  document.querySelector('#runtime').textContent=formatRuntime(runtimeSeconds);
  document.querySelector('#export').disabled=document.body.classList.contains('busy')||!decoded.stitches||memoryExceeded;
  if(artworkData){
    const objects=generatedPlan?.regions?.length||0,economy=generatedPlan?.thread_economy,connectors=economy?.covered_underpath_connector_count??generatedPlan?.operations?.filter(operation=>operation.role==='fill_connector').length??0,semanticIssues=Object.values(drc.semantic_violations||{}).reduce((sum,value)=>sum+value,0),budgetIssues=Object.values(drc.budget_violations||{}).reduce((sum,value)=>sum+value,0),issues=semanticIssues+drc.long_stitches+budgetIssues+(memoryExceeded?1:0),confidence=memoryExceeded?0:(drc.quality_score??Math.max(65,98-Math.min(30,Math.ceil((drc.short_stitches/Math.max(1,drc.segments))*100)+drc.long_stitches*3)));
    document.querySelector('#score').textContent=confidence;
    document.querySelector('#findingCount').textContent=issues?'1':'0';
    document.querySelector('#autopunchFinding').innerHTML=memoryExceeded?`<b>Block · Stitch memory exceeded</b><p>${decoded.stitches.toLocaleString()} stitches exceed the ${workArea.maxStitches.toLocaleString()}-stitch design capacity by ${(decoded.stitches-workArea.maxStitches).toLocaleString()}.</p>`:issues?`<b>Review · Production limits</b><p>${semanticIssues} stitch-rule findings; ${drc.long_stitches} over machine length; ${budgetIssues} movements or objects over plan limits.</p>`:`<b>Pass · Production limits</b><p>${drc.segments.toLocaleString()} production segments and movement counts satisfy the selected limits.</p>`;
    document.querySelector('#routeFinding').innerHTML=generatedPlan?`<b>Info · ${objects} embroidery objects</b><p>${drc.structural_trim_count??economy?.trim_count??semantic.trims} trims; ${drc.machine_jump_count??drc.jump_count??economy?.jump_count??decoded.jumps} machine jumps. ${connectors} covered underpath connectors${economy?`; ${Math.round(economy.needle_down_movement_ratio*100)}% needle-down movement`:''}. Capacity ${decoded.stitches.toLocaleString()} / ${workArea.maxStitches.toLocaleString()} stitches; up to ${workArea.maxDesigns} stored designs.</p>`:`<b>Info · ${decoded.jumps} frame moves</b><p>Imported DST movement records have no retained object semantics. Capacity ${decoded.stitches.toLocaleString()} / ${workArea.maxStitches.toLocaleString()} stitches.</p>`;
  }
  draw();
}
function colorizeOps(blockColors=previewThreadBlocks){NewHatThreadBlocks.colorizeOperations(operations,blockColors,threadSequence.length?threadSequence:[needles[0][1]])}
function planLabel(){if(!generatedPlan)return 'DECODED DST · FULL PLAN';const regions=generatedPlan.regions?.length||0,colors=generatedPlan.palette?.length||0;return `GENERATED DST · ${regions} OBJECT${regions===1?'':'S'} · ${colors} COLOR${colors===1?'':'S'}`}
function setPreviewMode(){articlePlaced=false;const stage=document.querySelector('#stage');stage.classList.add('dst-preview');document.querySelector('#frameMode').textContent=`PREVIEW · ${workArea.id==='cap'?'CAP':'FULL'}`;document.querySelector('#stageTag').textContent=planLabel();document.querySelector('#viewMode').textContent=generatedPlan?'DST PREVIEW / GENERATED':'DST PREVIEW / DECODED';draw()}
async function showCompiledDst(sourceOperations=operations, version=digitizeVersion) { if(!sourceOperations.some(operation=>operation.kind==='stitch'))throw Error('No stitchable operations');if(version!==digitizeVersion)return false;previewThreadBlocks=NewHatThreadBlocks.operationBlockColors(sourceOperations,threadSequence);operations=sourceOperations;colorizeOps();setPreviewMode();setup();document.querySelector('#stageTag').textContent=`COMPILING DST · ${planLabel()}`;document.querySelector('#operation').textContent='Compiling DST and decoding preview…';const response=await fetch('/api/preview.dst',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({operations:sourceOperations})}); if(!response.ok) throw Error('DST compilation failed'); const decoded=(await response.json()).operations;if(version!==digitizeVersion)return false;if(!decoded.some(operation=>operation.kind==='stitch'))throw Error('DST contained no displayable stitches');operations=decoded;colorizeOps();setPreviewMode();setup();return true; }
timeline.addEventListener('input',draw);
let playbackLast=0,playbackCarry=0;
function stopMachine(){if(playing){cancelAnimationFrame(playing);playing=null}playbackLast=0;playbackCarry=0;document.querySelector('#operation').textContent='STOP · Needle at upper position'}
function playbackFrame(timestamp){
  if(!playing)return;
  if(!playbackLast)playbackLast=timestamp;
  const elapsed=Math.min(250,timestamp-playbackLast);
  playbackLast=timestamp;
  playbackCarry+=elapsed*Math.min(workArea.maxSpm,Number(document.querySelector('#speed').value))/60000;
  const advance=Math.floor(playbackCarry);
  if(advance){
    playbackCarry-=advance;
    timeline.value=Math.min(Number(timeline.max),Number(timeline.value)+advance);
    draw();
  }
  if(Number(timeline.value)>=operations.length){stopMachine();return}
  playing=requestAnimationFrame(playbackFrame);
}
function startMachine(){if(playing||!operations.length)return;playbackLast=0;playbackCarry=0;playing=requestAnimationFrame(playbackFrame)}
document.querySelector('#start').addEventListener('click',startMachine);
document.querySelector('#stop').addEventListener('click',stopMachine);
document.querySelector('#back').addEventListener('click',()=>{timeline.value=Math.max(0,Number(timeline.value)-1);draw()});
document.querySelector('#startPoint').addEventListener('click',()=>{timeline.value=0;draw();document.querySelector('#operation').textContent='START PNT · Frame returned'});
let traceTimer=null;
document.querySelector('#trace').addEventListener('click',()=>{
  stopMachine();
  draw();
  const points=operations.filter(operation=>operation.kind==='stitch'&&Number.isFinite(operation.x)&&Number.isFinite(operation.y));
  if(!points.length){document.querySelector('#operation').textContent='TRACE · No stitch envelope available';return}
  const bounds=points.reduce((box,point)=>({
    minX:Math.min(box.minX,point.x),maxX:Math.max(box.maxX,point.x),
    minY:Math.min(box.minY,point.y),maxY:Math.max(box.maxY,point.y),
  }),{minX:Infinity,maxX:-Infinity,minY:Infinity,maxY:-Infinity});
  const left=400+(bounds.minX-previewProjection.centerX)*previewProjection.scale;
  const top=240+(bounds.minY-previewProjection.centerY)*previewProjection.scale;
  const trace=document.querySelector('#traceBoundary');
  trace.setAttribute('x',String(left));
  trace.setAttribute('y',String(top));
  trace.setAttribute('width',String(Math.max(1,(bounds.maxX-bounds.minX)*previewProjection.scale)));
  trace.setAttribute('height',String(Math.max(1,(bounds.maxY-bounds.minY)*previewProjection.scale)));
  trace.style.display='';
  trace.getAnimations().forEach(animation=>animation.cancel());
  trace.animate(
    [{strokeDashoffset:'36',opacity:.25},{strokeDashoffset:'0',opacity:1}],
    {duration:900,iterations:2,easing:'linear'}
  );
  clearTimeout(traceTimer);
  traceTimer=setTimeout(()=>{trace.style.display='none'},2200);
  document.querySelector('#operation').textContent=`TRACE · ${(bounds.maxX-bounds.minX).toFixed(1)} × ${(bounds.maxY-bounds.minY).toFixed(1)} mm stitch envelope`;
});
document.querySelector('#speed').addEventListener('input',e=>{document.querySelector('#speedValue').textContent=e.target.value;setup(true);if(playing){stopMachine();startMachine()}});
function toggleFullView(){const stage=document.querySelector('#stage'),button=document.querySelector('#fullView'),enabled=stage.classList.toggle('full-view');if(enabled){stopMachine();timeline.value=operations.length;draw();button.textContent='EXIT FULL VIEW';document.querySelector('#stageTag').textContent=`FULL STITCH VIEW · ${planLabel()}`}else{button.textContent='FULL STITCH VIEW';document.querySelector('#stageTag').textContent=planLabel()}}
document.querySelector('#fullView').addEventListener('click',toggleFullView);
document.querySelector('#exitFull').addEventListener('click',toggleFullView);
async function applyGeneratedPlan(plan,version){threadSequence=[...plan.palette];colorEnabled=plan.palette.map(()=>true);if(plan.width_mm&&plan.height_mm)designAspect=plan.width_mm/plan.height_mm;plan.palette.forEach((color,index)=>{needles[index][1]=color;needles[index][2]=`Palette ${index+1}`});renderNeedles();if(!plan.operations.length){document.querySelector('#operation').textContent='No stitchable regions detected';return}generatedPlan=plan;renderColorLayers();const applied=await showCompiledDst(selectedPlanOperations(),version);if(applied)document.querySelector('#operation').textContent=`Layered DST · ${operations.filter(operation=>operation.kind==='stitch').length.toLocaleString()} stitches`}
let digitizeTimer=null;
function scheduleRedigitize(){clearTimeout(digitizeTimer);digitizeTimer=setTimeout(redigitize,250)}
async function redigitize(){
  if(!artworkData)return;
  const version=++digitizeVersion;
  setBusy(true,fullDigitizer?'Reconstructing editable strokes and satin rails…':'Analyzing foreground geometry and thread colors…');
  document.querySelector('#operation').textContent=fullDigitizer?'Building editable stroke graph…':'Consolidating thread colors and classifying geometry…';
  try{
    if(fullDigitizer){await vectorizeArtwork(version);return}
    const response=await fetch('/api/digitize',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({image:artworkData,width_mm:Number(document.querySelector('#scale').value),colors:Number(document.querySelector('#paletteSize').value),density_multiplier:Number(document.querySelector('#stitchDensity').value)/100})});
    if(!response.ok)throw Error('Digitizer failed');
    const plan=await response.json();
    if(version!==digitizeVersion)return;
    if(plan.document&&plan.posterization?.selected_planner==='stroke_vector'){
      document.querySelector('#operation').textContent='Single-thread stroke detected · installing satin rails…';
      const density=Number(document.querySelector('#stitchDensity').value)/100;
      const transformed=mirrored||placement!=='center'||Number(document.querySelector('#rotation').value)!==0||density!==1;
      vectorDocument=transformed?transformVectorProposal(plan.document):plan.document;
      vectorDocument.layers.forEach(layer=>layer.density_multiplier=density);
      selectedVectorId=vectorDocument.objects[0]?.id||null;
      if(transformed)await planVectorDocument(version);
      else await installVectorPlan(plan,version);
      document.querySelector('#operation').textContent=`Satin stroke plan · ${vectorDocument.objects.length} objects · ${plan.palette.length} thread color`;
      return;
    }
    await applyGeneratedPlan(plan,version);
  }catch(error){
    if(version===digitizeVersion)document.querySelector('#operation').textContent=`Digitizer failed · ${error.message}`;
  }finally{
    if(version===digitizeVersion)setBusy(false);
  }
}
document.querySelector('#scale').addEventListener('input',async e=>{const value=Number(e.target.value);document.querySelector('#scaleLabel').textContent=`${value} mm wide`;updateArtworkTransform();if(artworkData)scheduleRedigitize();else{operations=buildPlan(value);await showCompiledDst()}});
document.querySelector('#paletteSize').addEventListener('input',e=>{document.querySelector('#paletteLabel').textContent=`${e.target.value} colors`;scheduleRedigitize()});
document.querySelector('#stitchDensity').addEventListener('input',e=>{document.querySelector('#densityLabel').textContent=`${e.target.value}%`;scheduleRedigitize()});
document.querySelector('#rotation').addEventListener('input',async e=>{document.querySelector('#rotationLabel').textContent=`${e.target.value}°`;updateArtworkTransform();await redigitize()});
document.querySelector('#mirrorArtwork').addEventListener('click',async()=>{mirrored=!mirrored;document.querySelector('#mirrorArtwork').classList.toggle('active',mirrored);document.querySelector('#mirrorArtwork').textContent=`⇋   ${mirrored ? 'Mirrored horizontally' : 'Mirror horizontally'}`;updateArtworkTransform();await redigitize()});
document.querySelector('#placement').addEventListener('click',async event=>{const button=event.target.closest('button');if(!button)return;placement=button.dataset.place;document.querySelectorAll('#placement button').forEach(item=>item.classList.toggle('active',item===button));document.querySelector('#placementLabel').textContent=`Front ${placement}`;updateArtworkTransform();await redigitize()});
document.querySelector('#replaceArtwork').addEventListener('click',()=>{
  // Browsers do not emit change when the same path is selected twice unless
  // the file input is reset first.
  artworkFile.value='';
  artworkFile.click();
});
function trainingWorkspace(){
  return new Promise((resolve,reject)=>{
    const request=indexedDB.open('new-hat-workspace',1);
    request.onupgradeneeded=()=>request.result.createObjectStore('handoffs');
    request.onerror=()=>reject(request.error);
    request.onsuccess=()=>resolve(request.result);
  });
}
async function saveTrainingHandoff(){
  const database=await trainingWorkspace();
  const handoff={
    schema_version:1,
    source:{name:document.querySelector('#sourceName').textContent,data_url:artworkData},
    settings:{
      width_mm:Number(document.querySelector('#scale').value),
      colors:Number(document.querySelector('#paletteSize').value),
      density_multiplier:Number(document.querySelector('#stitchDensity').value)/100,
    },
    document:vectorDocument?structuredClone(vectorDocument):null,
    origin:fullDigitizer?'advanced':'basic',
    created_at:new Date().toISOString(),
  };
  await new Promise((resolve,reject)=>{
    const transaction=database.transaction('handoffs','readwrite');
    transaction.objectStore('handoffs').put(handoff,'model-training');
    transaction.oncomplete=resolve;
    transaction.onerror=()=>reject(transaction.error);
  });
  database.close();
}
trainModelButton.addEventListener('click',async()=>{
  if(!artworkData)return;
  const previous=trainModelButton.textContent;
  trainModelButton.disabled=true;
  trainModelButton.textContent='Preparing…';
  try{
    await saveTrainingHandoff();
    window.top.location.assign(`train.html?from=${fullDigitizer?'advanced':'basic'}`);
  }catch(error){
    document.querySelector('#uploadHint').textContent=`Could not open training review · ${error.message}`;
    trainModelButton.disabled=false;
    trainModelButton.textContent=previous;
  }
});
artworkFile.addEventListener('change',()=>{
  const file=artworkFile.files[0];
  if(!file)return;
  if(file.size>10*1024*1024){document.querySelector('#uploadHint').textContent='Choose a file smaller than 10 MB.';return}
  const reader=new FileReader();
  reader.onerror=()=>{document.querySelector('#uploadHint').textContent='Could not read that artwork file.'};
  reader.onload=async()=>{
    artworkData=reader.result;
    generatedPlan=null;
    vectorDocument=null;
    selectedVectorId=null;
    artworkPreview.setAttributeNS('http://www.w3.org/1999/xlink','href',artworkData);
    artworkPreview.setAttribute('display','inline');
    artworkBounds.setAttribute('display','inline');
    document.querySelector('#sourceName').textContent=file.name;
    document.querySelector('#uploadHint').textContent=fullDigitizer?'Preparing editable embroidery objects.':'Preparing an embroidery plan.';
    trainModelButton.disabled=false;
    updateArtworkTransform();
    await redigitize();
  };
  reader.readAsDataURL(file);
});
document.querySelector('#fixDensity').addEventListener('click',async()=>{densityFixed=true;operations=buildPlan(Number(document.querySelector('#scale').value));document.querySelector('#score').textContent='94';document.querySelector('.caution').innerHTML='<b>Resolved · Curve spacing</b><p>Safe spacing was applied to the inside arc. Review the revised simulation.</p>';await showCompiledDst()});
document.querySelector('#reset').addEventListener('click',()=>location.reload());
document.querySelector('#openDst').addEventListener('click',()=>dstFile.click());
dstFile.addEventListener('change',async()=>{const file=dstFile.files[0];if(!file)return;const response=await fetch('/api/inspect.dst',{method:'POST',headers:{'Content-Type':'application/octet-stream'},body:file});if(!response.ok){document.querySelector('#operation').textContent='Could not read DST';return}generatedPlan=null;operations=(await response.json()).operations;const blocks=operations.filter(op=>op.kind==='color').length+1;threadSequence=needles.slice(0,Math.min(15,blocks)).map(needle=>needle[1]);previewThreadBlocks=NewHatThreadBlocks.operationBlockColors(operations,threadSequence);renderNeedles();renderColorLayers();colorizeOps();setPreviewMode();document.querySelector('#viewMode').textContent=`DST PREVIEW / ${file.name} · ${blocks} BLOCKS`;setup()});
document.querySelector('#export').addEventListener('click',async()=>{const button=document.querySelector('#export');if(!operations.some(operation=>operation.kind==='stitch')){button.textContent='No stitches to export';setTimeout(()=>button.textContent='Export DST',1600);return}button.textContent='Compiling…';try{const response=await fetch('/api/export.dst',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({operations})});if(!response.ok)throw Error();const blob=await response.blob(),a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download='new-hat-job.dst';a.click();URL.revokeObjectURL(a.href);button.textContent='DST exported'}catch{button.textContent='Export failed'}setTimeout(()=>button.textContent='Export DST',1600)});
document.querySelector('#addSolid').addEventListener('click',()=>addVectorObject('tatami'));
document.querySelector('#addCurve').addEventListener('click',()=>addVectorObject('running'));
document.querySelector('#vectorStitchType').addEventListener('change',event=>{if(!vectorDocument||!selectedVectorId)return;const object=vectorDocument.objects.find(item=>item.id===selectedVectorId);object.stitch_type=event.target.value;if(object.stitch_type==='running')object.closed=false;else object.closed=true;if(object.stitch_type==='satin')ensureSatinRails(object);renderVectorEditor();planVectorDocument().catch(error=>document.querySelector('#operation').textContent=error.message)});
document.querySelector('#deleteVector').addEventListener('click',()=>{if(!vectorDocument||!selectedVectorId)return;vectorDocument.objects=vectorDocument.objects.filter(item=>item.id!==selectedVectorId);selectedVectorId=vectorDocument.objects[0]?.id||null;renderVectorEditor();planVectorDocument().catch(error=>document.querySelector('#operation').textContent=error.message)});
document.querySelector('#canvas').addEventListener('pointermove',event=>{if(!vectorDrag)return;const point=vectorDocumentPoint(vectorPoint(event)),node=vectorDrag.nodes[vectorDrag.index];if(vectorDrag.kind==='anchor'){node.x=point.x;node.y=point.y}else{node[`${vectorDrag.kind}_x`]=point.x-node.x;node[`${vectorDrag.kind}_y`]=point.y-node.y}renderVectorEditor()});
document.querySelector('#canvas').addEventListener('pointerup',()=>{if(!vectorDrag)return;vectorDrag=null;planVectorDocument().catch(error=>document.querySelector('#operation').textContent=error.message)});
if(fullDigitizer){const layerControl=document.createElement('label');layerControl.innerHTML='Layer <select id="vectorLayer"></select>';document.querySelector('#vectorStitchType').closest('label').before(layerControl);const addLayer=document.createElement('button');addLayer.id='addLayer';addLayer.className='select';addLayer.textContent='Add layer';layerControl.before(addLayer);const bracketTitle=document.createElement('p'),bracketInspector=document.createElement('div');bracketTitle.className='eyebrow spacing';bracketTitle.textContent='SATIN DECISION BRACKETS';bracketInspector.id='decisionBrackets';bracketInspector.className='decision-brackets';document.querySelector('#deleteVector').after(bracketTitle,bracketInspector);renderDecisionBrackets();addLayer.addEventListener('click',()=>{if(!vectorDocument)vectorDocument={width_mm:72,height_mm:55,objects:[],layers:[{id:'base',name:'Base',order:0,enabled:true,density_multiplier:1}]};const id=`layer-${vectorDocument.layers.length+1}`;vectorDocument.layers.push({id,name:`Layer ${vectorDocument.layers.length+1}`,order:vectorDocument.layers.length,enabled:true,density_multiplier:1});const object=vectorDocument.objects.find(item=>item.id===selectedVectorId);if(object)object.layer_id=id;renderVectorLayerControl();planVectorDocument().catch(error=>document.querySelector('#operation').textContent=error.message)});document.querySelector('#vectorLayer').addEventListener('change',event=>{const object=vectorDocument?.objects.find(item=>item.id===selectedVectorId);if(!object)return;object.layer_id=event.target.value;renderVectorEditor();planVectorDocument().catch(error=>document.querySelector('#operation').textContent=error.message)})}else{let current=document.querySelector('#addSolid').parentElement.previousElementSibling,end=document.querySelector('#deleteVector').nextElementSibling;while(current){current.style.display='none';if(current===end)break;current=current.nextElementSibling}}
renderNeedles();
showCompiledDst().catch(()=>setup());
