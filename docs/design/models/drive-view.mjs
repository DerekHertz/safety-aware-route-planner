export const title = "Planning and driving";
const routeName = { default: "default route", alternative: "alternative", rerouted: "rerouted route" };
export const state = (v) => {
  if (v.screen === "planning") return `Planning, ${routeName[v.route]} selected`;
  if (v.screen === "driving") {
    const where = v.tapped ? "recomputing" : v.guidance === "offRoute" ? "off route" : "following";
    return `Driving the ${routeName[v.route]}, ${where}`;
  }
  if (v.screen === "arrived") return `Arrived (${v.feed})`;
  return v.reportSent ? "Drive report sent" : "Done, no report";
};
export const group = (v) => (v.screen === "planning" ? "plan" : v.screen === "driving" ? "drive" : "end");
export const colors = { plan: "blue", drive: "orange", end: "teal" };
export const step = (a, from, to) => {
  if (a.startsWith("Finished")) return null;
  return {
    ChooseAlternative: "you pick the alternative",
    ChooseDefault: "you pick the default",
    Start: "you press Start",
    DriftOff: "you leave the route",
    BackOnRoute: "you rejoin the route",
    TapRecompute: "you tap recompute",
    RerouteArrives: "the new route arrives",
    Arrive: "you reach the destination",
    SendReport: "you answer the crossing questions",
    SkipReport: "you skip the questions",
  }[a.split("(")[0]] ?? a;
};
