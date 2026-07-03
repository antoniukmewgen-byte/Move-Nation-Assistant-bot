// Entry point — imported modules wire up their own event listeners as a
// side effect of being evaluated; this file just needs to pull them all in
// (dependency order is handled by the import graph) and kick off bootstrap.
import "./telegram.js";
import { bootstrap } from "./onboarding.js";

bootstrap();
