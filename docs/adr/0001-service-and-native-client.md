# The engine is a Python service; the iOS app is a thin client over a committed OpenAPI file

Route comparison and assessment run in a Python service (FastAPI, Pydantic) built in WSL, and
the in-car experience is a native iOS app built on a Mac. Keeping the engine off the phone keeps
the Jev and provider keys off the device, lets Jev answers be cached and pre-run, and keeps the
10-second deadline in one place. The two sides share only `api/openapi.json`, generated from the
Pydantic models and committed, because they are built in different environments by different
sessions; neither reads the other's code.

## Considered Options

- Engine in Swift on the phone: rejected because it would ship secrets and lose the shared cache.
- A phone web app: rejected in favour of native, which can keep navigating in the background.
