const handoffKey='model-training';
const candidateTemplate=document.querySelector('#candidateTemplate');
const candidatesElement=document.querySelector('#candidates');
const sourceArtwork=document.querySelector('#sourceArtwork');
const artworkFile=document.querySelector('#artworkFile');
const state={
  handoff:null,
  base:null,
  parameter:null,
  candidates:[],
  positive:null,
  disposition:'incomplete',
  tieCandidates:new Set(),
  negatives:new Set(),
  reasons:new Set(),
  colorAccuracy:null,
  generation:0,
  experiments:new Map(),
  sessionId:null,
  sessionExported:false,
  currentStateId:null,
  acceptedParameters:{},
  transitionIds:[],
  candidateReviews:new Map(),
  blindMode:true,
  blindSeed:0,
  reviewStartedAt:null,
};

const REVIEW_AXES=[
  ['silhouette_fidelity','Silhouette'],
  ['detail_retention','Detail'],
  ['boundary_smoothness','Boundary'],
  ['lettering_legibility','Legibility'],
  ['negative_space_integrity','Negative space'],
  ['coverage','Coverage'],
  ['stitch_family_suitability','Stitch family'],
  ['color_fidelity','Color'],
  ['thread_economy','Thread economy'],
  ['path_efficiency','Path efficiency'],
  ['visible_defect_severity','Visible defects'],
  ['production_acceptability','Production'],
];

const PHASES={
  separation:'01 · COLOR SEPARATION',
  interpretation:'02 · OBJECT / STACKUP',
  construction:'03 · STITCH CONSTRUCTION',
};

const PARAMETERS={
  color_separation:{
    phase:'separation',
    label:'Shade consolidation',
    short:'Separation policy',
    reference:'Related shades → one physical thread',
    copy:'Brackets the merge tolerance and edge-fragment policy before geometry is split into color objects. Compare both the posterized art and the resulting sewout.',
    values:[
      {id:'literal',label:'Literal shades',merge:0,fragmented:0},
      {id:'near_exact',label:'Near-exact merge',merge:4,fragmented:0},
      {id:'tonal',label:'Tonal grouping',merge:8,fragmented:0},
      {id:'broad',label:'Broad grouping',merge:20,fragmented:0},
      {id:'thread',label:'Thread consolidation',merge:8,fragmented:.20,baseline:true},
    ],
    applies:()=>true,
  },
  posterization_smoothing:{
    phase:'separation',
    label:'Boundary cleanup',
    short:'Smoothing radius',
    reference:'0–2 px source-space Gaussian',
    copy:'Brackets silhouette smoothing before vectorization. Prefer the least smoothing that removes raster chatter while retaining counters, corners, stroke weight, and small authored details.',
    values:[
      {id:'none',label:'None · 0 px',smoothing:0,baseline:true},
      {id:'light',label:'Light · 0.50 px',smoothing:.5},
      {id:'balanced',label:'Balanced · 0.85 px',smoothing:.85},
      {id:'smooth',label:'Smooth · 1.25 px',smoothing:1.25},
      {id:'strong',label:'Strong · 2.00 px',smoothing:2},
    ],
    applies:()=>true,
  },
  stitch_family:{
    phase:'interpretation',
    label:'Stitch-family stack',
    short:'Top construction',
    reference:'Satin / tatami / layered running',
    copy:'Keeps the separated artwork fixed while comparing distinct physical interpretations of narrow and irregular objects.',
    values:[
      {id:'auto',label:'Automatic satin',mode:'auto',baseline:true},
      {id:'tatami',label:'Tatami blocking',mode:'tatami'},
      {id:'multi_run',label:'3-pass running',mode:'multi_run',passes:3},
    ],
    applies:()=>true,
  },
  tatami_density:{
    phase:'construction',
    label:'Tatami infill density',
    short:'Tatami density',
    reference:'0.60–1.50× profile spacing',
    copy:'Forces the same separated objects through tatami construction and brackets only the fill-row density.',
    values:[.70,.85,1,1.15,1.30].map(value=>({
      id:String(value),label:`${Math.round(value*100)}%`,value,
      baseline:value===1,
    })),
    applies:()=>true,
  },
  satin_spacing_mm:{
    phase:'construction',
    label:'Satin course spacing',
    short:'Satin spacing',
    reference:'0.40–0.44 mm',
    copy:'Uses the same satin rails and changes only the distance between courses to expose coverage, curve, and thread-economy tradeoffs.',
    values:[],
    applies:document=>document?.objects?.some(object=>object.stitch_type==='satin'),
  },
  running_passes:{
    phase:'construction',
    label:'Running-stitch stack',
    short:'Running passes',
    reference:'1–5 alternating passes',
    copy:'Compares single running paths with multiple direction-alternating passes over the same extracted centerline.',
    values:[1,2,3,4,5].map(value=>({
      id:String(value),label:`${value} pass${value===1?'':'es'}`,value,
      baseline:value===3,
    })),
    applies:()=>true,
  },
};

function experimentValues(parameter){
  if(parameter!=='satin_spacing_mm')return PARAMETERS[parameter].values;
  const satin=(state.base?.document?.objects||[]).filter(object=>object.stitch_type==='satin');
  const values=satin.map(object=>Number(object.satin_spacing_mm)).filter(Number.isFinite).sort((left,right)=>left-right);
  const baseline=values.length?values[Math.floor(values.length/2)]:.42;
  return [-.08,-.04,0,.04,.08].map(offset=>{
    const value=Number(Math.max(.2,Math.min(.8,baseline+offset)).toFixed(3));
    return {
      id:String(value),
      label:`${value.toFixed(2)} mm`,
      value,
      baseline:offset===0,
    };
  });
}

function workspace(){
  return new Promise((resolve,reject)=>{
    const request=indexedDB.open('new-hat-workspace',1);
    request.onupgradeneeded=()=>request.result.createObjectStore('handoffs');
    request.onerror=()=>reject(request.error);
    request.onsuccess=()=>resolve(request.result);
  });
}

async function readHandoff(){
  const database=await workspace();
  const value=await new Promise((resolve,reject)=>{
    const transaction=database.transaction('handoffs','readonly');
    const request=transaction.objectStore('handoffs').get(handoffKey);
    request.onsuccess=()=>resolve(request.result);
    request.onerror=()=>reject(request.error);
  });
  database.close();
  return value;
}

async function writeHandoff(value){
  const database=await workspace();
  await new Promise((resolve,reject)=>{
    const transaction=database.transaction('handoffs','readwrite');
    transaction.objectStore('handoffs').put(value,handoffKey);
    transaction.oncomplete=resolve;
    transaction.onerror=()=>reject(transaction.error);
  });
  database.close();
}

function updateSource(){
  const handoff=state.handoff;
  const frame=document.querySelector('.source-frame');
  if(!handoff?.source?.data_url){
    frame.classList.remove('has-art');
    return;
  }
  sourceArtwork.src=handoff.source.data_url;
  frame.classList.add('has-art');
  document.querySelector('#sourceName').textContent=handoff.source.name||'Untitled artwork';
  document.querySelector('#sourceSize').textContent=`${Number(handoff.settings.width_mm).toFixed(0)} mm wide · ${handoff.settings.colors} color target`;
}

function baselineParameters(){
  const settings=state.handoff.settings;
  return {
    requested_width_mm:Number(settings.width_mm),
    requested_colors:Number(settings.colors),
    profile:settings.profile||'standard',
    density_multiplier:Number(settings.density_multiplier||1),
    color_merge_tolerance:8,
    fragmented_color_threshold:.20,
    posterization_smoothing_px:0,
    construction_mode:'auto',
    running_passes:3,
    satin_spacing_mm:null,
  };
}

function compactParameters(parameters){
  return Object.fromEntries(
    Object.entries(parameters).filter(([,value])=>value!==undefined)
  );
}

async function requestJson(url,body){
  const response=await fetch(url,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  if(!response.ok){
    const responseText=await response.text();
    let message=responseText||'Planner request failed';
    if(/<html[\s>]/i.test(responseText)){
      const errorPage=new DOMParser().parseFromString(responseText,'text/html');
      const detail=[...errorPage.querySelectorAll('p')]
        .map(item=>item.textContent.trim())
        .find(item=>item.startsWith('Message:'));
      if(detail)message=detail.replace(/^Message:\s*/,'');
    }
    const error=Error(message);
    error.status=response.status;
    throw error;
  }
  return response.json();
}

function updateSessionControls(message=''){
  const completed=state.experiments.size;
  const total=state.base?availableParameters().length:Object.keys(PARAMETERS).length;
  document.querySelector('#sessionProgress').textContent=message||(
    completed
      ?`${completed} of ${total} experiments cached for this session`
      :'No experiments cached'
  );
  const available=Boolean(completed);
  const exportButton=document.querySelector('#exportSession');
  exportButton.disabled=!available;
  exportButton.textContent=completed?`Export session · ${completed}`:'Export session';
  document.querySelector('#discardSession').disabled=!state.sessionId&&!completed;
}

async function ensureTrainingSession(){
  if(state.sessionId)return state.sessionId;
  const result=await requestJson('/api/training/session/create',{
    schema_version:1,
    source:state.handoff.source,
    settings:state.handoff.settings,
    origin:state.handoff.origin,
    rights:state.handoff.rights||{},
    production_context:state.handoff.production_context||{},
    baseline_resolved_parameters:baselineParameters(),
  });
  state.sessionId=result.session_id;
  state.sessionExported=false;
  state.currentStateId=result.baseline_state_id;
  state.acceptedParameters={
    ...baselineParameters(),
    ...(result.baseline_resolved_parameters||{}),
  };
  state.handoff.training_session_id=state.sessionId;
  state.handoff.training_state={
    state_id:state.currentStateId,
    resolved_parameters:state.acceptedParameters,
    transition_ids:state.transitionIds,
  };
  await writeHandoff(state.handoff);
  updateSessionControls();
  return state.sessionId;
}

function candidateParameters(candidate){
  return compactParameters({
    color_merge_tolerance:candidate.option.merge,
    fragmented_color_threshold:candidate.option.fragmented,
    posterization_smoothing_px:candidate.option.smoothing,
    construction_mode:candidate.option.mode,
    density_multiplier:candidate.parameter==='tatami_density'?candidate.option.value:undefined,
    running_passes:(
      candidate.parameter==='running_passes'
        ?candidate.option.value
        :candidate.parameter==='stitch_family'
          ?candidate.option.passes
          :undefined
    ),
    satin_spacing_mm:candidate.parameter==='satin_spacing_mm'?candidate.option.value:undefined,
  });
}

function candidateResolvedParameters(candidate){
  return {
    ...baselineParameters(),
    ...state.acceptedParameters,
    ...candidateParameters(candidate),
  };
}

function candidateMetricRecord(candidate){
  return {
    stitches:candidate.metrics.stitches,
    trims:candidate.metrics.trims,
    jumps:candidate.metrics.jumps,
    bracket_blockers:candidate.metrics.blockers,
    bracket_warnings:candidate.metrics.warnings,
    status:candidate.metrics.status,
    color_count:candidate.metrics.palette.length,
    palette:candidate.metrics.palette,
    quantized_color_count:candidate.metrics.separation.quantized_color_count,
    merged_shade_count:candidate.metrics.separation.merged_shade_count,
    mean_color_error_rgb:candidate.metrics.separation.mean_color_error_rgb,
    geometry_quality:candidate.metrics.geometry,
    stitch_types:candidate.metrics.stitchTypes,
    object_count:candidate.metrics.objects,
  };
}

function currentReview(){
  const reviewed=state.candidates
    .filter(candidate=>candidateReviewState(candidate.id).reviewed)
    .map(candidate=>candidate.id);
  const ranking=state.candidates
    .map(candidate=>({
      id:candidate.id,
      rank:candidateReviewState(candidate.id).rank,
    }))
    .filter(item=>item.rank!==null)
    .sort((left,right)=>left.rank-right.rank)
    .map(item=>item.id);
  const candidateScores=Object.fromEntries(
    state.candidates.map(candidate=>[
      candidate.id,
      {...candidateReviewState(candidate.id).scores},
    ])
  );
  const candidateDefects=Object.fromEntries(
    state.candidates.map(candidate=>[
      candidate.id,
      [...candidateReviewState(candidate.id).defects],
    ])
  );
  const reviewStates=state.candidates.map(
    candidate=>candidateReviewState(candidate.id)
  );
  const allScored=reviewStates.every(review=>
    REVIEW_AXES.every(([axis])=>Number.isInteger(review.scores[axis]))
  );
  const ranks=reviewStates.map(review=>review.rank).filter(Number.isInteger);
  const completeRanking=(
    ranks.length===state.candidates.length
    &&new Set(ranks).size===state.candidates.length
    &&Math.min(...ranks)===1
    &&Math.max(...ranks)===state.candidates.length
  );
  const outcomeComplete=(
    (state.disposition==='winner'&&Boolean(state.positive))
    ||(state.disposition==='tie'&&state.tieCandidates.size>=2)
    ||state.disposition==='no_acceptable_candidate'
  );
  return {
    positive_candidate_id:state.positive,
    tie_candidate_ids:[...state.tieCandidates],
    negative_candidate_ids:[...state.negatives],
    reviewed_candidate_ids:reviewed,
    completion_state:(
      reviewed.length===state.candidates.length
      &&allScored
      &&completeRanking
      &&outcomeComplete
        ?'complete'
        :'partial'
    ),
    disposition:state.disposition,
    ranking,
    candidate_scores:candidateScores,
    candidate_defects:candidateDefects,
    blind_display_order:state.candidates.map(candidate=>candidate.id),
    blind_order_seed:state.blindSeed,
    review_ui_version:'new-hat-training-ui/2',
    rubric_version:'embroidery-review/2.0.0',
    review_duration_ms:(
      state.reviewStartedAt===null
        ?null
        :Math.round(performance.now()-state.reviewStartedAt)
    ),
    reasons:[...state.reasons],
    color_accuracy:state.colorAccuracy,
    note:document.querySelector('#reviewNote').value.trim(),
  };
}

async function adoptSelectedCandidate(experiment,candidate){
  const stateAfter=`state_${crypto.randomUUID().replaceAll('-','')}`;
  const transitionId=`transition_${crypto.randomUUID().replaceAll('-','')}`;
  experiment.review={
    ...experiment.review,
    transition:{
      status:'accepted',
      state_before:experiment.parentStateId,
      state_after:stateAfter,
      transition_id:transitionId,
      candidate_id:candidate.id,
    },
  };
  await persistExperiment(experiment.parameter);
  state.currentStateId=stateAfter;
  state.acceptedParameters={
    ...candidate.resolvedParameters,
  };
  state.transitionIds=[
    ...state.transitionIds,
    transitionId,
  ];
  for(const [parameter,cached] of [...state.experiments]){
    if(
      parameter!==experiment.parameter
      &&cached.parentStateId!==state.currentStateId
      &&!cached.review?.transition
    ){
      releaseCandidateMemory(cached.candidates);
      state.experiments.delete(parameter);
    }
  }
  state.handoff.training_state={
    state_id:state.currentStateId,
    resolved_parameters:state.acceptedParameters,
    transition_ids:state.transitionIds,
  };
  await writeHandoff(state.handoff);
  renderParameters();
}

function captureCurrentExperiment(){
  const experiment=state.experiments.get(state.parameter);
  if(experiment)experiment.review={
    ...currentReview(),
    ...(experiment.review?.training_record_id
      ?{training_record_id:experiment.review.training_record_id}
      :{}),
  };
}

function releaseCandidateMemory(candidates){
  candidates.forEach(candidate=>{
    if(candidate.payload?.posterization)delete candidate.payload.posterization.preview_data_url;
    delete candidate.posterizedDataUrl;
    delete candidate.sewoutDataUrl;
  });
}

async function cleanupTrainingSession({clearMemory=false}={}){
  const sessionId=state.sessionId;
  if(sessionId){
    await requestJson('/api/training/session/cleanup',{session_id:sessionId});
  }
  state.sessionId=null;
  state.currentStateId=null;
  state.acceptedParameters={};
  state.transitionIds=[];
  state.positive=null;
  state.disposition='incomplete';
  state.tieCandidates.clear();
  if(state.handoff)delete state.handoff.training_session_id;
  if(state.handoff)delete state.handoff.training_state;
  if(clearMemory){
    state.experiments.forEach(experiment=>releaseCandidateMemory(experiment.candidates));
    state.experiments.clear();
    state.candidates=[];
    candidatesElement.replaceChildren();
  }
  updateSessionControls();
}

function rasterRequest(overrides={}){
  const settings=state.handoff.settings;
  return {
    image:state.handoff.source.data_url,
    width_mm:settings.width_mm,
    colors:settings.colors,
    density_multiplier:settings.density_multiplier,
    color_merge_tolerance:8,
    fragmented_color_threshold:.20,
    posterization_smoothing_px:0,
    include_posterized:true,
    ...compactParameters({
      density_multiplier:state.acceptedParameters.density_multiplier,
      color_merge_tolerance:state.acceptedParameters.color_merge_tolerance,
      fragmented_color_threshold:state.acceptedParameters.fragmented_color_threshold,
      posterization_smoothing_px:state.acceptedParameters.posterization_smoothing_px,
      construction_mode:state.acceptedParameters.construction_mode,
      running_passes:state.acceptedParameters.running_passes,
    }),
    ...overrides,
  };
}

async function ensureBase(){
  if(state.base)return state.base;
  const raster=await requestJson('/api/digitize',rasterRequest());
  if(state.handoff.document){
    const payload=await requestJson('/api/vector/plan',state.handoff.document);
    state.base={
      ...payload,
      document:payload.document||state.handoff.document,
      palette:[...new Set(state.handoff.document.objects.filter(item=>item.enabled!==false).map(item=>item.color))],
      posterization:raster.posterization,
    };
    return state.base;
  }
  state.base=raster;
  return state.base;
}

function availableParameters(){
  const document=state.base?.document;
  return Object.entries(PARAMETERS).filter(([,parameter])=>parameter.applies(document));
}

function renderParameters(){
  const container=document.querySelector('#parameterList');
  container.replaceChildren();
  let activePhase=null;
  availableParameters().forEach(([id,parameter])=>{
    if(parameter.phase!==activePhase){
      activePhase=parameter.phase;
      const phase=document.createElement('p');
      phase.className='phase-label';
      phase.role='presentation';
      phase.textContent=PHASES[activePhase];
      container.append(phase);
    }
    const button=document.createElement('button');
    button.type='button';
    button.role='tab';
    button.ariaSelected=String(state.parameter===id);
    button.classList.toggle('completed',state.experiments.has(id));
    button.innerHTML=`${parameter.label}<span>${parameter.reference}</span>`;
    button.addEventListener('click',()=>selectParameter(id));
    container.append(button);
  });
  const parameter=PARAMETERS[state.parameter];
  const phase=parameter?.phase||'construction';
  const review=document.querySelector('.review');
  review.dataset.phase=phase;
  document.querySelector('#bracketKind').textContent=(
    phase==='separation'?'BRACKETED POSTERIZATIONS':'BRACKETED SEWOUTS'
  );
  document.querySelector('#referenceRange').textContent=parameter?.reference||'—';
  document.querySelector('#referenceCopy').textContent=parameter?.copy||'The bracket keeps every other planner input fixed.';
  document.querySelector('#bracketTitle').textContent=parameter?`${PHASES[parameter.phase]} · ${parameter.label}`:'Controlled comparison';
  updateSessionControls();
}

function mutateDocument(source,parameter,value){
  const document=structuredClone(source);
  if(parameter==='satin_spacing_mm'){
    document.objects
      .filter(object=>object.stitch_type==='satin')
      .forEach(object=>object.satin_spacing_mm=value);
  }
  return document;
}

async function buildCandidate(parameter,option,index){
  const startedAt=new Date().toISOString();
  const startedClock=performance.now();
  const base=state.base;
  let payload;
  let document=base.document||null;
  if(parameter==='color_separation'){
    payload=await requestJson('/api/digitize',rasterRequest({
      color_merge_tolerance:option.merge,
      fragmented_color_threshold:option.fragmented,
    }));
    document=payload.document||null;
  }else if(parameter==='posterization_smoothing'){
    payload=await requestJson('/api/digitize',rasterRequest({
      posterization_smoothing_px:option.smoothing,
    }));
    document=payload.document||null;
  }else if(parameter==='stitch_family'){
    payload=await requestJson('/api/digitize',rasterRequest({
      construction_mode:option.mode,
      running_passes:option.passes||3,
    }));
    document=payload.document||null;
  }else if(parameter==='tatami_density'){
    payload=await requestJson('/api/digitize',rasterRequest({
      construction_mode:'tatami',
      density_multiplier:option.value,
    }));
    document=null;
  }else if(parameter==='running_passes'){
    payload=await requestJson('/api/digitize',rasterRequest({
      construction_mode:'multi_run',
      running_passes:option.value,
    }));
    document=null;
  }else if(document&&parameter==='satin_spacing_mm'){
    document=mutateDocument(document,parameter,option.value);
    payload=await requestJson('/api/vector/plan',document);
    payload={
      ...payload,
      document,
      palette:[...new Set(document.objects.filter(item=>item.enabled!==false).map(item=>item.color))],
      posterization:base.posterization,
    };
  }else{
    throw Error(`Experiment ${parameter} is not actuated for this plan`);
  }
  const candidate={
    id:`${parameter}-${option.id}`,
    index,
    value:option.id,
    option,
    parameter,
    phase:PARAMETERS[parameter].phase,
    payload,
    document,
    generation:{
      started_at_utc:startedAt,
      completed_at_utc:new Date().toISOString(),
      duration_ms:Math.round(performance.now()-startedClock),
      peak_memory_bytes:null,
    },
  };
  candidate.actionDelta=candidateParameters(candidate);
  candidate.resolvedParameters=candidateResolvedParameters(candidate);
  return candidate;
}

function planMetrics(candidate){
  const operations=candidate.payload.operations||[];
  const stitches=operations.filter(operation=>operation.kind==='stitch').length;
  const trims=operations.filter(operation=>operation.kind==='trim').length;
  const jumps=operations.filter(operation=>operation.kind==='jump').length;
  const brackets=candidate.payload.brackets||[];
  const blockers=brackets.filter(item=>item.status==='blocker').length;
  const warnings=brackets.filter(item=>['warning','fallback','near_limit'].includes(item.status)).length;
  const drc=candidate.payload.drc||{};
  const long=Number(drc.long_stitches||0);
  const status=(blockers||long)?'block':warnings?'warning':'pass';
  const palette=[...new Set(candidate.payload.palette||[])];
  const separation=candidate.payload.posterization?.color_separation||{};
  const geometry=candidate.payload.posterization?.geometry_quality||{};
  const stitchTypes=(candidate.payload.regions||[]).reduce((counts,region)=>{
    const type=region.stitch_type==='fill'?'tatami':region.stitch_type;
    counts[type]=(counts[type]||0)+1;
    return counts;
  },{});
  return {
    stitches,trims,jumps,brackets,blockers,warnings,long,status,palette,
    separation,geometry,stitchTypes,objects:(candidate.payload.regions||[]).length,
  };
}

function candidateFingerprint(candidate){
  let hash=2166136261;
  const update=value=>{
    for(const character of String(value)){
      hash^=character.charCodeAt(0);
      hash=Math.imul(hash,16777619);
    }
  };
  if(candidate.phase==='separation'){
    update(candidate.parameter);
    update(candidate.option.id);
    update(candidate.payload.posterization?.preview_data_url||'');
  }
  (candidate.payload.palette||[]).forEach(update);
  (candidate.payload.operations||[]).forEach(operation=>{
    update(operation.kind);
    update(operation.role||'');
    update(Number(operation.x||0).toFixed(2));
    update(Number(operation.y||0).toFixed(2));
  });
  return `${hash>>>0}:${candidate.payload.operations?.length||0}`;
}

function operationColors(candidate){
  const colorObjects=[
    ...(candidate.payload.regions||[]),
    ...(candidate.document?.objects||candidate.payload.document?.objects||[]),
  ];
  const objectColors=new Map(colorObjects.flatMap(object=>[
    [object.id,object.color],
    [object.region_id,object.color],
  ]).filter(([identifier,color])=>identifier&&color));
  const palette=candidate.payload.palette?.length?candidate.payload.palette:['#e85d8e'];
  let colorIndex=0;
  return (candidate.payload.operations||[]).map(operation=>{
    if(operation.kind==='color')colorIndex=Math.min(colorIndex+1,palette.length-1);
    return objectColors.get(operation.region_id)||palette[colorIndex]||'#e85d8e';
  });
}

function drawSewout(canvas,candidate){
  const context=canvas.getContext('2d');
  const width=canvas.width,height=canvas.height;
  context.fillStyle='#132e33';
  context.fillRect(0,0,width,height);
  context.strokeStyle='#ffffff0d';
  context.lineWidth=1;
  for(let x=0;x<width;x+=24){context.beginPath();context.moveTo(x,0);context.lineTo(x,height);context.stroke()}
  for(let y=0;y<height;y+=24){context.beginPath();context.moveTo(0,y);context.lineTo(width,y);context.stroke()}
  const operations=candidate.payload.operations||[];
  const positioned=operations.filter(operation=>Number.isFinite(operation.x)&&Number.isFinite(operation.y));
  const stitches=positioned.filter(operation=>operation.kind==='stitch');
  if(!stitches.length)return;
  const bounds=stitches.reduce((box,operation)=>({
    minX:Math.min(box.minX,operation.x),maxX:Math.max(box.maxX,operation.x),
    minY:Math.min(box.minY,operation.y),maxY:Math.max(box.maxY,operation.y),
  }),{minX:Infinity,maxX:-Infinity,minY:Infinity,maxY:-Infinity});
  const scale=Math.min((width-54)/Math.max(1,bounds.maxX-bounds.minX),(height-54)/Math.max(1,bounds.maxY-bounds.minY));
  const offsetX=(width-(bounds.maxX-bounds.minX)*scale)/2-bounds.minX*scale;
  const offsetY=(height-(bounds.maxY-bounds.minY)*scale)/2-bounds.minY*scale;
  const colors=operationColors(candidate);
  let previous=null,breakPath=true;
  operations.forEach((operation,index)=>{
    if(operation.kind!=='stitch'){
      if(['jump','trim','color'].includes(operation.kind))breakPath=true;
      return;
    }
    const point={x:operation.x*scale+offsetX,y:operation.y*scale+offsetY};
    if(previous&&!breakPath){
      context.beginPath();
      context.moveTo(previous.x,previous.y);
      context.lineTo(point.x,point.y);
      context.strokeStyle=colors[index];
      context.globalAlpha=operation.role?.includes('underlay')?.40:.92;
      context.lineWidth=operation.role?.includes('underlay')?.75:1.15;
      context.stroke();
    }
    previous=point;
    breakPath=false;
  });
  context.globalAlpha=1;
}

function evidenceText(metrics){
  if(metrics.status==='block')return `${metrics.blockers+metrics.long} blocking checks · ${metrics.warnings} review checks`;
  if(metrics.status==='warning')return `${metrics.warnings} decision brackets need review · no hard machine block`;
  return `${metrics.brackets.length} decision brackets pass · no blocking evidence`;
}

function phaseEvidence(candidate,metrics){
  if(candidate.parameter==='posterization_smoothing'){
    const geometry=metrics.geometry;
    return `${Number(geometry.corners_per_100_boundary_px||0).toFixed(1)} edge turns / 100 px · ${geometry.component_count||0} components · ${geometry.small_component_count||0} specks · ${geometry.negative_space_count||0} retained negative spaces · ${(Number(geometry.reference_iou||0)*100).toFixed(1)}% silhouette overlap`;
  }
  if(candidate.phase==='separation'){
    return `${metrics.separation.quantized_color_count??metrics.palette.length} quantized shades → ${metrics.palette.length} thread color${metrics.palette.length===1?'':'s'} · ${metrics.separation.merged_shade_count||0} merged · mean RGB error ${Number(metrics.separation.mean_color_error_rgb||0).toFixed(1)}`;
  }
  if(candidate.parameter==='stitch_family'){
    const types=Object.entries(metrics.stitchTypes).map(([type,count])=>`${count} ${type}`).join(' · ');
    return `${types||'No stitchable objects'} · ${metrics.objects} total objects`;
  }
  return evidenceText(metrics);
}

function metricMarkup(candidate,metrics){
  const common=[
    ['Stitches',metrics.stitches.toLocaleString()],
    ['Objects',metrics.objects.toLocaleString()],
  ];
  if(candidate.phase==='separation'){
    if(candidate.parameter==='posterization_smoothing'){
      return [
        ['Edge turns / 100 px',Number(metrics.geometry.corners_per_100_boundary_px||0).toFixed(1)],
        ['Components',String(metrics.geometry.component_count||0)],
        ['Negative spaces',String(metrics.geometry.negative_space_count||0)],
        ['Silhouette overlap',`${(Number(metrics.geometry.reference_iou||0)*100).toFixed(1)}%`],
      ];
    }
    return [
      ...common,
      ['Threads',metrics.palette.length.toLocaleString()],
      ['Shades merged',String(metrics.separation.merged_shade_count||0)],
    ];
  }
  return [
    ...common,
    ['Trims',metrics.trims.toLocaleString()],
    ['Jumps',metrics.jumps.toLocaleString()],
  ];
}

function candidateReviewState(candidateId){
  if(!state.candidateReviews.has(candidateId)){
    state.candidateReviews.set(candidateId,{
      reviewed:false,
      rank:null,
      scores:{},
      defects:[],
    });
  }
  return state.candidateReviews.get(candidateId);
}

function renderCandidate(candidate){
  const fragment=candidateTemplate.content.cloneNode(true);
  const card=fragment.querySelector('.candidate');
  card.dataset.candidateId=candidate.id;
  fragment.querySelector('.candidate-letter').textContent=String.fromCharCode(65+candidate.index);
  fragment.querySelector('.candidate-kind').textContent=candidate.option.baseline?'Current baseline':PHASES[candidate.phase];
  fragment.querySelector('.candidate-parameter span').textContent=PARAMETERS[candidate.parameter].short;
  fragment.querySelector('.candidate-parameter b').textContent=candidate.option.label;
  const posterized=fragment.querySelector('.posterized-frame img');
  posterized.src=candidate.payload.posterization?.preview_data_url||state.handoff.source.data_url;
  if(candidate.phase==='separation'){
    fragment.querySelector('.posterized-frame>span').textContent='POSTERIZED CANDIDATE';
    fragment.querySelector('.sewout-frame .visual-label').textContent='DOWNSTREAM SEWOUT';
  }
  const metrics=planMetrics(candidate);
  candidate.metrics=metrics;
  const paletteElement=fragment.querySelector('.candidate-palette');
  metrics.palette.forEach(color=>{
    const swatch=document.createElement('i');
    swatch.style.backgroundColor=color;
    swatch.title=color.toUpperCase();
    paletteElement.append(swatch);
  });
  fragment.querySelector('.candidate-metrics').innerHTML=metricMarkup(
    candidate,metrics
  ).map(([label,value])=>`<div><span>${label}</span><b>${value}</b></div>`).join('');
  const evidence=fragment.querySelector('.candidate-evidence');
  evidence.classList.add(metrics.status);
  evidence.textContent=phaseEvidence(candidate,metrics);
  const positive=fragment.querySelector('.positive');
  const negative=fragment.querySelector('.negative');
  const reviewed=fragment.querySelector('.reviewed');
  const reviewState=candidateReviewState(candidate.id);
  const scoring=fragment.querySelector('.candidate-scoring');
  const details=document.createElement('details');
  const summary=document.createElement('summary');
  const scoreGrid=document.createElement('div');
  summary.textContent='Score candidate · anchored 1–5';
  scoreGrid.className='score-grid';
  const rankLabel=document.createElement('label');
  const rankSelect=document.createElement('select');
  rankLabel.className='rank-control';
  rankLabel.textContent='Overall rank';
  rankSelect.innerHTML='<option value="">Unranked</option>'+state.candidates.map(
    (_,index)=>`<option value="${index+1}">${index+1}</option>`
  ).join('');
  rankSelect.value=reviewState.rank||'';
  rankSelect.addEventListener('change',()=>{
    reviewState.rank=rankSelect.value?Number(rankSelect.value):null;
    reviewState.reviewed=true;
    refreshSelections();
  });
  rankLabel.append(rankSelect);
  scoreGrid.append(rankLabel);
  REVIEW_AXES.forEach(([axis,label])=>{
    const field=document.createElement('label');
    const select=document.createElement('select');
    field.textContent=label;
    const anchors=(
      axis==='visible_defect_severity'
        ?['None','Minor','Moderate','Major','Severe']
        :['Poor','Weak','Acceptable','Strong','Excellent']
    );
    select.innerHTML='<option value="">Not scored</option>'+
      anchors.map(
        (anchor,index)=>`<option value="${index+1}">${index+1} · ${anchor}</option>`
      ).join('');
    select.value=reviewState.scores[axis]||'';
    select.addEventListener('change',()=>{
      if(select.value)reviewState.scores[axis]=Number(select.value);
      else delete reviewState.scores[axis];
      reviewState.reviewed=true;
      refreshSelections();
    });
    field.append(select);
    scoreGrid.append(field);
  });
  details.append(summary,scoreGrid);
  scoring.append(details);
  positive.addEventListener('click',()=>choosePositive(candidate.id));
  negative.addEventListener('click',()=>toggleNegative(candidate.id));
  reviewed.addEventListener('click',()=>{
    reviewState.reviewed=!reviewState.reviewed;
    refreshSelections();
  });
  candidatesElement.append(fragment);
  const canvas=card.querySelector('canvas');
  drawSewout(canvas,candidate);
  candidate.posterizedDataUrl=posterized.src;
  candidate.sewoutDataUrl=canvas.toDataURL('image/png');
  card.classList.add('ready');
}

function restoreReview(review={}){
  state.positive=review.positive_candidate_id||null;
  state.disposition=review.disposition||(
    state.positive?'winner':'incomplete'
  );
  state.tieCandidates=new Set(review.tie_candidate_ids||[]);
  state.negatives=new Set(review.negative_candidate_ids||[]);
  state.reasons=new Set(review.reasons||[]);
  state.colorAccuracy=review.color_accuracy||null;
  state.candidateReviews=new Map();
  const reviewed=new Set(review.reviewed_candidate_ids||[]);
  const ranking=review.ranking||[];
  state.candidates.forEach(candidate=>{
    state.candidateReviews.set(candidate.id,{
      reviewed:reviewed.has(candidate.id),
      rank:(
        ranking.includes(candidate.id)
          ?ranking.indexOf(candidate.id)+1
          :null
      ),
      scores:{...(review.candidate_scores?.[candidate.id]||{})},
      defects:[...(review.candidate_defects?.[candidate.id]||[])],
    });
  });
  document.querySelector('#reviewNote').value=review.note||'';
  document.querySelectorAll('#reasonList button').forEach(button=>{
    button.classList.toggle('active',state.reasons.has(button.dataset.reason));
  });
  document.querySelectorAll('#colorAccuracy button').forEach(button=>{
    button.classList.toggle('active',button.dataset.accuracy===state.colorAccuracy);
  });
  document.querySelectorAll('#reviewDisposition button').forEach(button=>{
    button.classList.toggle(
      'active',button.dataset.disposition===state.disposition
    );
  });
}

function renderExperiment(experiment,{cached=false}={}){
  candidatesElement.replaceChildren();
  state.candidates=experiment.candidates;
  state.blindSeed=experiment.randomSeed||0;
  state.reviewStartedAt=performance.now();
  restoreReview(experiment.review);
  state.candidates.forEach((candidate,index)=>{
    candidate.index=index;
    renderCandidate(candidate);
  });
  document.querySelector('#bracketSummary').textContent=`${state.candidates.length} distinct alternatives${experiment.equivalentCount?` · ${experiment.equivalentCount} equivalent ${experiment.equivalentCount===1?'plan':'plans'} collapsed`:''} · ${cached?'restored from this session':'saved for cross-phase review'}`;
  refreshSelections();
}

function artifactPlan(candidate){
  const plan=structuredClone(candidate.payload);
  if(plan.posterization)delete plan.posterization.preview_data_url;
  return plan;
}

async function persistExperiment(parameter,{recoverMissingSession=true}={}){
  const experiment=state.experiments.get(parameter);
  if(!experiment)return;
  const sessionId=await ensureTrainingSession();
  const cards=new Map(
    [...document.querySelectorAll('.candidate')].map(card=>[
      card.dataset.candidateId,
      card,
    ])
  );
  const candidates=experiment.candidates.map(candidate=>{
    const card=cards.get(candidate.id);
    const currentCanvas=card?.querySelector('canvas');
    if(currentCanvas)candidate.sewoutDataUrl=currentCanvas.toDataURL('image/png');
    candidate.posterizedDataUrl=(
      candidate.payload.posterization?.preview_data_url
      ||candidate.posterizedDataUrl
      ||state.handoff.source.data_url
    );
    return {
      candidate_id:candidate.id,
      value:candidate.value,
      parameters:candidateParameters(candidate),
      action_delta:candidate.actionDelta||candidateParameters(candidate),
      resolved_parameters:(
        candidate.resolvedParameters
        ||candidateResolvedParameters(candidate)
      ),
      metrics:candidateMetricRecord(candidate),
      generation:candidate.generation,
      plan:artifactPlan(candidate),
      posterized_data_url:candidate.posterizedDataUrl,
      sewout_data_url:candidate.sewoutDataUrl,
    };
  });
  let result;
  try{
    result=await requestJson('/api/training/session/experiment',{
      schema_version:1,
      session_id:sessionId,
      experiment:{
        phase:PARAMETERS[parameter].phase,
        parameter,
        reference:PARAMETERS[parameter].reference,
        parent_state_id:experiment.parentStateId,
        parent_resolved_parameters:experiment.parentResolvedParameters,
        random_seed:experiment.randomSeed||0,
        candidates,
        review:experiment.review,
      },
    });
  }catch(error){
    if(
      recoverMissingSession
      &&error.status===400
      &&/not found|already cleared/i.test(error.message)
    ){
      state.sessionId=null;
      delete state.handoff.training_session_id;
      await writeHandoff(state.handoff);
      return persistExperiment(parameter,{recoverMissingSession:false});
    }
    throw error;
  }
  if(result.recovered_without_source&&recoverMissingSession){
    await cleanupTrainingSession();
    return persistExperiment(parameter,{recoverMissingSession:false});
  }
  experiment.persisted=true;
  updateSessionControls(
    `${result.experiment_count} experiment${result.experiment_count===1?'':'s'} cached · plans, DSTs, parameters, and renders ready`
  );
}

function refreshSelections(){
  document.querySelectorAll('.candidate').forEach(card=>{
    const id=card.dataset.candidateId;
    const positive=id===state.positive;
    const tied=state.tieCandidates.has(id);
    const negative=state.negatives.has(id);
    card.classList.toggle('selected-positive',positive);
    card.classList.toggle('selected-negative',negative);
    const positiveButton=card.querySelector('.positive');
    const negativeButton=card.querySelector('.negative');
    const reviewedButton=card.querySelector('.reviewed');
    const reviewed=candidateReviewState(id).reviewed;
    positiveButton.classList.toggle(
      'active',state.disposition==='tie'?tied:positive
    );
    positiveButton.disabled=(
      state.disposition==='no_acceptable_candidate'
    );
    positiveButton.textContent=(
      state.disposition==='tie'
        ?(tied?'Included in tie':'Include in tie')
        :(positive?'Positive example':'Use as positive')
    );
    negativeButton.classList.toggle('active',negative);
    negativeButton.textContent=negative?'Marked negative':'Mark negative';
    reviewedButton.classList.toggle('active',reviewed);
    reviewedButton.textContent=reviewed?'Reviewed':'Mark reviewed';
  });
  const positive=state.candidates.find(item=>item.id===state.positive);
  const reviews=state.candidates.map(
    candidate=>candidateReviewState(candidate.id)
  );
  const reviewedCount=reviews.filter(review=>review.reviewed).length;
  const allScored=reviews.every(review=>
    REVIEW_AXES.every(([axis])=>Number.isInteger(review.scores[axis]))
  );
  const ranks=reviews.map(review=>review.rank).filter(Number.isInteger);
  const completeRanking=(
    ranks.length===state.candidates.length
    &&new Set(ranks).size===state.candidates.length
    &&Math.min(...ranks)===1
    &&Math.max(...ranks)===state.candidates.length
  );
  const outcomeReady=(
    (state.disposition==='winner'&&Boolean(positive))
    ||(state.disposition==='tie'&&state.tieCandidates.size>=2)
    ||state.disposition==='no_acceptable_candidate'
  );
  document.querySelector('#selectionSummary').textContent=(
    state.disposition==='tie'
      ?`${state.tieCandidates.size} tied candidates · ${reviewedCount}/${state.candidates.length} reviewed`
      :state.disposition==='no_acceptable_candidate'
        ?`No candidate accepted · ${reviewedCount}/${state.candidates.length} reviewed`
        :positive
          ?`Candidate ${String.fromCharCode(65+positive.index)} preferred · ${reviewedCount}/${state.candidates.length} reviewed`
          :`Choose one positive candidate · ${reviewedCount}/${state.candidates.length} reviewed`
  );
  document.querySelector('#saveSignal').disabled=(
    !outcomeReady
    ||!state.reasons.size
    ||!state.colorAccuracy
    ||reviewedCount!==state.candidates.length
    ||!allScored
    ||!completeRanking
  );
}

function choosePositive(id){
  if(state.disposition==='tie'){
    if(state.tieCandidates.has(id))state.tieCandidates.delete(id);
    else state.tieCandidates.add(id);
    candidateReviewState(id).reviewed=true;
    refreshSelections();
    return;
  }
  if(state.positive!==id)resetColorAccuracy();
  state.disposition='winner';
  document.querySelectorAll('#reviewDisposition button').forEach(button=>{
    button.classList.toggle(
      'active',button.dataset.disposition==='winner'
    );
  });
  state.positive=id;
  state.tieCandidates.clear();
  state.candidates.forEach(candidate=>{
    if(
      candidate.id!==id
      &&candidateReviewState(candidate.id).rank===1
    ){
      candidateReviewState(candidate.id).rank=null;
      const card=document.querySelector(
        `.candidate[data-candidate-id="${CSS.escape(candidate.id)}"]`
      );
      if(card)card.querySelector('.rank-control select').value='';
    }
  });
  candidateReviewState(id).reviewed=true;
  candidateReviewState(id).rank=1;
  const positiveCard=document.querySelector(
    `.candidate[data-candidate-id="${CSS.escape(id)}"]`
  );
  if(positiveCard)positiveCard.querySelector('.rank-control select').value='1';
  state.negatives.delete(id);
  refreshSelections();
}

function toggleNegative(id){
  if(state.positive===id){
    state.positive=null;
    resetColorAccuracy();
  }
  state.negatives.has(id)?state.negatives.delete(id):state.negatives.add(id);
  refreshSelections();
}

function shuffled(values,seed){
  const result=[...values];
  let stateValue=seed>>>0;
  const random=()=>{
    stateValue=(Math.imul(stateValue,1664525)+1013904223)>>>0;
    return stateValue/4294967296;
  };
  for(let index=result.length-1;index>0;index--){
    const swap=Math.floor(random()*(index+1));
    [result[index],result[swap]]=[result[swap],result[index]];
  }
  return result;
}

async function selectParameter(parameter){
  const previousParameter=state.parameter;
  captureCurrentExperiment();
  if(
    previousParameter
    && previousParameter!==parameter
    && state.experiments.has(previousParameter)
  ){
    try{
      await persistExperiment(previousParameter);
    }catch(error){
      updateSessionControls(`Previous phase remains in memory · cache update failed: ${error.message}`);
    }
  }
  state.parameter=parameter;
  state.positive=null;
  state.disposition='incomplete';
  state.tieCandidates.clear();
  resetColorAccuracy();
  state.negatives.clear();
  state.reasons.clear();
  document.querySelectorAll('#reasonList button').forEach(button=>button.classList.remove('active'));
  state.candidates=[];
  state.generation+=1;
  const version=state.generation;
  renderParameters();
  if(state.experiments.has(parameter)){
    renderExperiment(state.experiments.get(parameter),{cached:true});
    return;
  }
  candidatesElement.replaceChildren();
  state.blindSeed=crypto.getRandomValues(new Uint32Array(1))[0];
  const options=shuffled(
    experimentValues(parameter),
    state.blindSeed,
  );
  document.querySelector('#bracketSummary').textContent=`Generating ${options.length} phase-specific plans…`;
  for(let index=0;index<options.length;index++){
    const fragment=candidateTemplate.content.cloneNode(true);
    const card=fragment.querySelector('.candidate');
    card.dataset.placeholder=String(index);
    fragment.querySelector('.candidate-letter').textContent=String.fromCharCode(65+index);
    fragment.querySelector('.candidate-kind').textContent='Preparing bracket';
    candidatesElement.append(fragment);
  }
  const results=await Promise.allSettled(options.map((option,index)=>buildCandidate(parameter,option,index)));
  if(version!==state.generation)return;
  candidatesElement.replaceChildren();
  const fingerprints=new Set();
  let equivalentCount=0;
  results.forEach((result,index)=>{
    if(result.status==='fulfilled'){
      const fingerprint=candidateFingerprint(result.value);
      if(fingerprints.has(fingerprint)){
        equivalentCount+=1;
        return;
      }
      fingerprints.add(fingerprint);
      result.value.index=state.candidates.length;
      state.candidates.push(result.value);
    }else{
      const fragment=candidateTemplate.content.cloneNode(true);
      const card=fragment.querySelector('.candidate');
      card.classList.add('error');
      fragment.querySelector('.candidate-letter').textContent=String.fromCharCode(65+index);
      fragment.querySelector('.candidate-kind').textContent='Generation failed';
      candidatesElement.append(fragment);
    }
  });
  const experiment={
    parameter,
    phase:PARAMETERS[parameter].phase,
    candidates:state.candidates,
    parentStateId:state.currentStateId,
    parentResolvedParameters:structuredClone(state.acceptedParameters),
    randomSeed:state.blindSeed,
    equivalentCount,
    review:currentReview(),
    persisted:false,
  };
  state.experiments.set(parameter,experiment);
  renderParameters();
  renderExperiment(experiment);
  try{
    await persistExperiment(parameter);
  }catch(error){
    updateSessionControls(`Candidates retained in this window · artifact cache failed: ${error.message}`);
  }
}

async function sourceHash(dataUrl){
  const encoded=dataUrl.split(',',2)[1];
  const binary=atob(encoded);
  const bytes=Uint8Array.from(binary,character=>character.charCodeAt(0));
  const digest=await crypto.subtle.digest('SHA-256',bytes);
  return [...new Uint8Array(digest)].map(value=>value.toString(16).padStart(2,'0')).join('');
}

async function saveSignal(){
  const button=document.querySelector('#saveSignal');
  const status=document.querySelector('#saveStatus');
  button.disabled=true;
  button.textContent='Saving…';
  status.textContent='';
  try{
    const experiment=state.experiments.get(state.parameter);
    if(experiment){
      experiment.review=currentReview();
      await persistExperiment(state.parameter);
    }
    const structuredReview=currentReview();
    const payload={
      schema_version:1,
      training_session_id:state.sessionId,
      source:{
        name:state.handoff.source.name,
        sha256:await sourceHash(state.handoff.source.data_url),
      },
      context:{
        origin:state.handoff.origin,
        width_mm:state.handoff.settings.width_mm,
        colors:state.handoff.settings.colors,
      },
      comparison:{
        phase:PARAMETERS[state.parameter].phase,
        parameter:state.parameter,
        reference:PARAMETERS[state.parameter].reference,
        candidates:state.candidates.map(candidate=>({
          candidate_id:candidate.id,
          value:candidate.value,
          parameters:candidateParameters(candidate),
          metrics:candidateMetricRecord(candidate),
        })),
        blind_display_order:structuredReview.blind_display_order,
        blind_order_seed:structuredReview.blind_order_seed,
      },
      reinforcement:{
        positive_candidate_id:state.positive,
        tie_candidate_ids:[...state.tieCandidates],
        negative_candidate_ids:[...state.negatives],
        reasons:[...state.reasons],
        color_accuracy:state.colorAccuracy,
        note:document.querySelector('#reviewNote').value.trim(),
        reviewed_candidate_ids:structuredReview.reviewed_candidate_ids,
        completion_state:structuredReview.completion_state,
        disposition:structuredReview.disposition,
        ranking:structuredReview.ranking,
        candidate_scores:structuredReview.candidate_scores,
        candidate_defects:structuredReview.candidate_defects,
        review_duration_ms:structuredReview.review_duration_ms,
        review_ui_version:structuredReview.review_ui_version,
        rubric_version:structuredReview.rubric_version,
      },
    };
    const result=await requestJson('/api/training/feedback',payload);
    if(experiment){
      experiment.review={
        ...currentReview(),
        training_record_id:result.record_id,
      };
      const selected=experiment.candidates.find(
        candidate=>candidate.id===state.positive
      );
      if(selected&&structuredReview.disposition==='winner'){
        await adoptSelectedCandidate(experiment,selected);
      }else{
        await persistExperiment(state.parameter);
      }
    }
    button.textContent='Training signal saved';
    status.textContent=`Record ${result.record_id}`;
  }catch(error){
    button.disabled=false;
    button.textContent='Save training signal';
    status.textContent=`Could not save · ${error.message}`;
  }
}

document.querySelector('#reasonList').addEventListener('click',event=>{
  const button=event.target.closest('button');
  if(!button)return;
  const reason=button.dataset.reason;
  state.reasons.has(reason)?state.reasons.delete(reason):state.reasons.add(reason);
  button.classList.toggle('active',state.reasons.has(reason));
  refreshSelections();
});
document.querySelector('#reviewDisposition').addEventListener('click',event=>{
  const button=event.target.closest('button');
  if(!button)return;
  state.disposition=button.dataset.disposition;
  if(state.disposition!=='winner')state.positive=null;
  if(state.disposition!=='tie')state.tieCandidates.clear();
  document.querySelectorAll('#reviewDisposition button').forEach(item=>{
    item.classList.toggle('active',item===button);
  });
  refreshSelections();
});
function resetColorAccuracy(){
  state.colorAccuracy=null;
  document.querySelectorAll('#colorAccuracy button').forEach(button=>button.classList.remove('active'));
}
document.querySelector('#colorAccuracy').addEventListener('click',event=>{
  const button=event.target.closest('button');
  if(!button)return;
  state.colorAccuracy=button.dataset.accuracy;
  document.querySelectorAll('#colorAccuracy button').forEach(item=>item.classList.toggle('active',item===button));
  refreshSelections();
});
function renderReviewMode(){
  document.querySelector('.review').classList.toggle(
    'blind',state.blindMode
  );
  document.querySelector('#diagnosticMode').textContent=(
    state.blindMode?'Show diagnostics':'Return to blind view'
  );
}
document.querySelector('#diagnosticMode').addEventListener('click',()=>{
  state.blindMode=!state.blindMode;
  renderReviewMode();
});
document.querySelector('#saveSignal').addEventListener('click',saveSignal);
document.querySelector('#exportSession').addEventListener('click',async()=>{
  const button=document.querySelector('#exportSession');
  if(!state.experiments.size)return;
  captureCurrentExperiment();
  button.disabled=true;
  button.textContent='Packaging session…';
  try{
    for(const parameter of state.experiments.keys()){
      await persistExperiment(parameter);
    }
    const response=await fetch('/api/training/session/export',{
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({session_id:state.sessionId}),
    });
    if(!response.ok)throw Error((await response.text())||'Session export failed');
    const blob=await response.blob();
    const disposition=response.headers.get('Content-Disposition')||'';
    const filename=disposition.match(/filename="([^"]+)"/)?.[1]||'training-session.zip';
    const url=URL.createObjectURL(blob);
    const link=document.createElement('a');
    link.href=url;
    link.download=filename;
    link.click();
    setTimeout(()=>URL.revokeObjectURL(url),1000);
    state.sessionId=null;
    state.sessionExported=true;
    delete state.handoff.training_session_id;
    delete state.handoff.training_state;
    await writeHandoff(state.handoff);
    updateSessionControls('Session exported · temporary plans and sewouts deleted');
  }catch(error){
    button.disabled=false;
    button.textContent='Export session';
    updateSessionControls(`Could not export session · ${error.message}`);
  }
});
document.querySelector('#discardSession').addEventListener('click',async()=>{
  try{
    await cleanupTrainingSession({clearMemory:true});
    state.parameter=null;
    renderParameters();
    document.querySelector('#bracketSummary').textContent='Temporary session deleted';
    updateSessionControls('Temporary plans and sewouts deleted');
  }catch(error){
    updateSessionControls(`Could not delete temporary session · ${error.message}`);
  }
});
document.querySelector('#replaceArtwork').addEventListener('click',()=>{
  artworkFile.value='';
  artworkFile.click();
});
artworkFile.addEventListener('change',async()=>{
  const file=artworkFile.files[0];
  if(!file)return;
  if(file.size>10*1024*1024){
    document.querySelector('#bracketSummary').textContent='Choose artwork smaller than 10 MB.';
    return;
  }
  try{
    await cleanupTrainingSession({clearMemory:true});
  }catch{
    // The new source remains usable if a stale temporary session vanished.
  }
  const reader=new FileReader();
  reader.onload=async()=>{
    state.handoff={
      schema_version:1,
      source:{name:file.name,data_url:reader.result},
      settings:state.handoff?.settings||{width_mm:72,colors:8,density_multiplier:1},
      document:null,
      origin:new URLSearchParams(location.search).get('from')||'basic',
      created_at:new Date().toISOString(),
    };
    state.base=null;
    state.parameter=null;
    state.experiments.clear();
    state.sessionId=null;
    state.sessionExported=false;
    state.currentStateId=null;
    state.acceptedParameters={};
    state.transitionIds=[];
    state.positive=null;
    state.disposition='incomplete';
    state.tieCandidates.clear();
    await writeHandoff(state.handoff);
    updateSource();
    await initializePlanner();
  };
  reader.readAsDataURL(file);
});
async function initializePlanner(){
  if(!state.handoff?.source?.data_url)return;
  candidatesElement.innerHTML='<div class="initial-loading"><b>Interpreting embroidery objects…</b><span>Building the candidate parameter space.</span></div>';
  try{
    await ensureBase();
    const parameters=availableParameters();
    if(!parameters.length)throw Error('No tunable stitch objects were found in this artwork.');
    state.parameter='color_separation';
    renderParameters();
    await selectParameter(state.parameter);
  }catch(error){
    candidatesElement.innerHTML=`<div class="initial-loading error-message"><b>Could not prepare bracketed sewouts</b><span>${error.message}</span></div>`;
    document.querySelector('#bracketSummary').textContent='Planner failed';
  }
}

(async()=>{
  renderReviewMode();
  const origin=new URLSearchParams(location.search).get('from');
  const returnTarget=origin==='advanced'?'digitizer.html':'index.html';
  document.querySelector('#backLink').href=returnTarget;
  document.querySelector('#returnLink').href=returnTarget;
  try{
    state.handoff=await readHandoff();
    if(state.handoff?.training_session_id){
      state.sessionId=state.handoff.training_session_id;
      state.currentStateId=state.handoff.training_state?.state_id||null;
      state.acceptedParameters={
        ...baselineParameters(),
        ...(state.handoff.training_state?.resolved_parameters||{}),
      };
      state.transitionIds=[
        ...(state.handoff.training_state?.transition_ids||[]),
      ];
    }
    updateSource();
    await initializePlanner();
  }catch(error){
    document.querySelector('#bracketSummary').textContent=`Could not read artwork handoff · ${error.message}`;
  }
})();
