#!/bin/bash
# ══════════════════════════════════════════════════════════════
# lib_ask.sh — Saisie des réglages, utilisable en mode automatique.
#
# ask VARIABLE "Question" "valeur par défaut"
#
#   1) si VARIABLE est déjà définie dans l'environnement (fichier
#      hotspotpro.conf chargé par install.sh), elle est gardée telle quelle ;
#   2) sinon, si HOTSPOT_AUTO=1, la valeur par défaut est prise sans rien
#      demander (installation sans intervention) ;
#   3) sinon, la question est posée.
#
# ask_secret : identique, mais la saisie n'est pas affichée à l'écran.
# ask_pause  : pause « Appuyez sur Entrée », ignorée en mode automatique.
# ══════════════════════════════════════════════════════════════

ask() {
  local __var="$1" __prompt="$2" __default="${3:-}" __answer
  if [ -n "${!__var:-}" ]; then
    return 0
  fi
  if [ "${HOTSPOT_AUTO:-0}" = "1" ]; then
    printf -v "$__var" '%s' "$__default"
    return 0
  fi
  if [ -n "$__default" ]; then
    read -rp "  ▶ $__prompt [$__default] : " __answer
  else
    read -rp "  ▶ $__prompt : " __answer
  fi
  printf -v "$__var" '%s' "${__answer:-$__default}"
}

ask_secret() {
  local __var="$1" __prompt="$2" __default="${3:-}" __answer
  if [ -n "${!__var:-}" ]; then
    return 0
  fi
  if [ "${HOTSPOT_AUTO:-0}" = "1" ]; then
    printf -v "$__var" '%s' "$__default"
    return 0
  fi
  read -rsp "  ▶ $__prompt : " __answer
  echo
  printf -v "$__var" '%s' "${__answer:-$__default}"
}

ask_pause() {
  [ "${HOTSPOT_AUTO:-0}" = "1" ] && return 0
  read -rp "  ▶ ${1:-Appuyez sur Entrée pour continuer…} " _
}
