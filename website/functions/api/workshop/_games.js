// Canonical game registry for the Workshop — mirrors GS_CATALOG in ui/index.html
// (the app's switcher). The DB stores the slug (`game`), which also names the
// logo at ui/logos/<slug>.png; the API echoes the display `name` so the front
// end never hard-codes the slug→name mapping. Resolves design-doc §7.3.
//
// Keep in sync with ui/index.html GS_CATALOG when games are added.

export const GAMES = {
  f1_25:     { name: "F1 25 – F1 21",                accent: "#e10600", supported: true },
  dr2:       { name: "DiRT Rally 2.0",               accent: "#f5b02e", supported: true },
  forza:     { name: "Forza Horizon / Motorsport",   accent: "#2dd4bf", supported: true },
  ac:        { name: "Assetto Corsa",                accent: "#f43f5e", supported: true },
  acc:       { name: "Assetto Corsa Competizione",   accent: "#f43f5e", supported: true },
  iracing:   { name: "iRacing",                      accent: "#e8482e", supported: true },
  wrc:       { name: "EA SPORTS WRC",                accent: "#4f8ef7", supported: true },
  pcars2:    { name: "Project CARS 2",               accent: null,      supported: false },
  ams2:      { name: "Automobilista 2",              accent: null,      supported: false },
  f1manager: { name: "F1 Manager",                   accent: null,      supported: false },
};

// "*" = game-agnostic preset. Unknown slugs echo back as-is so a preset for a
// game the registry hasn't learned yet still renders (just without a pretty name).
export function gameName(slug) {
  if (slug === "*") return "Any game";
  // own-property check so "constructor"/"toString" don't resolve prototype members
  return isKnownGame(slug) ? GAMES[slug].name : slug;
}

export function isKnownGame(slug) {
  return slug === "*" || Object.prototype.hasOwnProperty.call(GAMES, slug);
}
