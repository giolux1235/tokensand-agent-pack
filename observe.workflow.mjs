import { activeProjects } from "./projects.mjs";
export default {
  cases: [{id:"ready",input:{items:["ready"],expected:1}}, {id:"archived",input:{items:["archived"],expected:0}}, {id:"mixed",input:{items:["ready","archived"],expected:1}}],
  maxCallsPerRun:{candidate:0}, checks:[{id:"active_count",label:"Count only active projects"}],
  candidate:({items})=>activeProjects(items), evaluate:({input,output})=>({active_count:output===input.expected})
};
