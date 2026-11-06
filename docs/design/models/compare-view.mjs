const done = {
  defaultOnly: "Default route only",
  checkFailed: "Crossings couldn't be checked",
  improvement: "Alternative shown",
  overBudget: "No improvement within budget",
  noImprovement: "No improvement found",
  incomplete: "Search incomplete",
};
export const title = "One comparison";
export const state = (v) => {
  if (v.phase === "routing") return "Getting the route";
  if (v.phase === "routeFailed") return "Route couldn't be obtained";
  if (v.phase === "checking") return "Checking the default's crossings";
  if (v.phase === "searching") return "Searching nearby crossings";
  if (v.phase === "ranking") return v.searchComplete ? "Comparing (search finished)" : "Comparing (deadline hit)";
  return done[v.outcome];
};
export const group = (v) =>
  v.phase === "done" || v.phase === "routeFailed" ? "result" : "working";
export const colors = { result: "teal", working: "blue" };
export const step = (a, from, to) => {
  if (a.startsWith("Finished")) return null;
  if (a.startsWith("RouteFails")) return "the routing source fails";
  if (a.startsWith("RouteOk")) return "the route arrives";
  if (a.startsWith("DefaultChecked")) {
    if (to.outcome === "checkFailed") return "a check failed";
    if (to.outcome === "defaultOnly") return "checks done [avoidance off]";
    if (to.outcome === "noImprovement") return "checks done [no flags to avoid]";
    return "checks done [avoidance on, flags found]";
  }
  if (a.startsWith("DeadlineHits")) return "10 seconds pass";
  if (a.startsWith("SearchFinishes")) return "every candidate is checked";
  if (a.startsWith("Rank")) return "the comparison decides";
  if (a.startsWith("Assess")) return "a crossing is assessed";
  if (a.startsWith("CheckFails")) return "a crossing check fails";
  if (a.startsWith("RouteCandidate")) return "a candidate route arrives";
  return a;
};
