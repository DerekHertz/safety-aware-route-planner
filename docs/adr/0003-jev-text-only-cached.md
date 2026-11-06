# Jev judges records as text, batched per route, cached and pre-run for the corridor

The spec framed Jev around street images, but TypeSafe marks image input as a preview not ready
for production and we have no image source yet. For the MVP Jev answers only three typed
questions from records: which approaches a control stops, whether a street is a main street, and
whether sources conflict. Rules handle anything a record answers directly. Questions for one route
go in one request, because Jev reads a shared state once. Answers are cached by pinned model
version and a hash of the evidence and question, which gives repeatable results that Jev's docs do
not promise, and the corridor is pre-run so Jev's unmeasured latency stays off the request path.
An answer stays a lead until Jev passes an automatic gate: agreeing with the rules on crossings
the rules settle from clear records. That gate tests how Jev reads records, not whether the records
match the street; drive reports cover the latter.
