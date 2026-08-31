"""
Module de Sécurité & Modération pour ProxiMatch.

Ce module fournit les filtres et règles de détection pour :
1. Détecter et contrer les tentatives de contournement de plateforme (partage de numéros, e-mails, réseaux sociaux, liens, IBAN).
2. Détecter et filtrer les contenus indésirables, injurieux, haineux ou spams.
3. Proposer des modes d'action modulables :
   - 'mask' : masquage automatique des coordonnées et contenus sensibles.
   - 'block' : blocage strict avec levée d'erreur.
   - 'audit' : simple journalisation/marquage (flag) sans modification.
"""

import os
import re
import unicodedata
from typing import Optional
from pydantic import BaseModel, Field


# ----------------------------------------------------------------------
# Modèles Pydantic pour la modération
# ----------------------------------------------------------------------
class ModerationCheckRequest(BaseModel):
    content: str = Field(..., min_length=1, description="Contenu du message à modérer")
    action_mode: Optional[str] = Field("mask", description="Mode d'action : 'mask', 'block' ou 'audit'")


class ViolationDetail(BaseModel):
    category: str
    description: str
    matched_fragment: Optional[str] = None


class ModerationResult(BaseModel):
    is_flagged: bool
    action: str  # "allow", "mask", "block"
    reasons: list[str]
    violations: list[ViolationDetail]
    original_content: str
    filtered_content: str


# ----------------------------------------------------------------------
# Expressions Régulières & Dictionnaires de Filtrage
# ----------------------------------------------------------------------

# 1. Numéros de téléphone (Formats FR, Internationaux, avec séparateurs ou espacés)
PHONE_REGEXES = [
    # Format international (+33, 0033) avec ou sans séparateurs
    re.compile(r'(?:\+|00)\s*33\s*(?:\(0\)\s*)?[1-9](?:[\s\.\-_/]*\d{2}){4}', re.IGNORECASE),
    # Format national français 06, 07, 01, etc. : 06 12 34 56 78 / 06.12.34.56.78 / 0612345678
    re.compile(r'\b0[1-9](?:[\s\.\-_/]*\d{2}){4}\b'),
    # Séquence de 10 chiffres consécutifs ou presque consécutifs
    re.compile(r'\b0[1-9](?:\d[\s\.\-_]?){8}\d\b'),
    # Détection de numéros épelés en toutes lettres (ex: zero six douze...)
    re.compile(
        r'\b(?:zero|zéro)\s+(?:six|sept|un|deux|trois|quatre|cinq|huit|neuf)(?:\s+(?:zero|zéro|un|deux|trois|quatre|cinq|six|sept|huit|neuf|\d+)){4,}\b',
        re.IGNORECASE,
    ),
]

# 2. Adresses e-mail (standards et obfusquées : user [at] domaine [dot] com, user arobase ...)
EMAIL_REGEXES = [
    re.compile(r'[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+', re.IGNORECASE),
    re.compile(r'[a-zA-Z0-9_.+-]+\s*(?:\[at\]|\(at\)|\bat\b|arobase|@)\s*[a-zA-Z0-9-]+(?:\s*(?:\[dot\]|\(dot\)|\bdot\b|point|\.)\s*[a-zA-Z]{2,})+', re.IGNORECASE),
]

# 3. Liens web, réseaux sociaux et messageries directes hors plateforme
BYPASS_LINK_REGEXES = [
    re.compile(r'https?://[^\s]+', re.IGNORECASE),
    re.compile(r'\b(?:www\.)[a-zA-Z0-9-]+\.[a-zA-Z]{2,}[^\s]*', re.IGNORECASE),
    re.compile(r'\b(?:t\.me|wa\.me|instagram\.com|facebook\.com|snapchat\.com|tiktok\.com|linkedin\.com)/[^\s]+', re.IGNORECASE),
    re.compile(r'\b(?:snap|insta|telegram|whatsapp|paypal|lydia)\s*:\s*@?[a-zA-Z0-9_.-]+', re.IGNORECASE),
]

# 4. Phrases types d'incitation au contournement (Disintermediation)
BYPASS_PHRASES = [
    "en direct sans l'app",
    "en direct sans l'application",
    "en dehors de l'app",
    "en dehors de la plateforme",
    "passe par whatsapp",
    "passe par telegram",
    "ecris moi sur whatsapp",
    "ecris moi sur telegram",
    "ecris-moi sur whatsapp",
    "ecris-moi sur telegram",
    "contacte moi en prive",
    "contacte-moi en prive",
    "contactez moi en prive",
    "contactez-moi en prive",
    "appelle moi directement",
    "appelle-moi directement",
    "paiement en especes sans declarer",
    "payer en liquide en direct",
    "hors commission",
]

# 5. Coordonnées bancaires directes (IBAN)
IBAN_REGEX = re.compile(r'\b[A-Z]{2}\d{2}[A-Z0-9]{4}\d{7}([A-Z0-9]?){0,16}\b', re.IGNORECASE)

# 6. Mots ou expressions inappropriées / toxiques / insultes
INAPPROPRIATE_WORDS = [
    "connard", "connasse", "salope", "pute", "encule", "enculer", "fdp",
    "batard", "merde", "putain", "trouduc", "salaud", "nique", "niquer",
    "escroc", "arnaqueur", "raciste", "suicide", "menace",
]


def normalize_text(text: str) -> str:
    """Normalise une chaîne de caractères (supprime les accents, passage en minuscules)."""
    text_normalized = unicodedata.normalize('NFD', text)
    text_without_accents = "".join(c for c in text_normalized if unicodedata.category(c) != 'Mn')
    return text_without_accents.lower()


def detect_violations(content: str) -> list[ViolationDetail]:
    """Analyse un message et renvoie la liste détaillée des infractions détectées."""
    violations: list[ViolationDetail] = []
    norm_content = normalize_text(content)

    # A. Détection Numéros de téléphone
    for rx in PHONE_REGEXES:
        for match in rx.finditer(content):
            violations.append(
                ViolationDetail(
                    category="phone_number",
                    description="Numéro de téléphone détecté (contournement de plateforme)",
                    matched_fragment=match.group(0),
                )
            )

    # B. Détection E-mails
    for rx in EMAIL_REGEXES:
        for match in rx.finditer(content):
            violations.append(
                ViolationDetail(
                    category="email",
                    description="Adresse e-mail détectée (contournement de plateforme)",
                    matched_fragment=match.group(0),
                )
            )

    # C. Détection Liens & Réseaux Sociaux
    for rx in BYPASS_LINK_REGEXES:
        for match in rx.finditer(content):
            violations.append(
                ViolationDetail(
                    category="external_link",
                    description="Lien externe ou identifiant réseau social détecté",
                    matched_fragment=match.group(0),
                )
            )

    # D. Détection IBAN
    for match in IBAN_REGEX.finditer(content):
        # Vérifie un minimum de longueur pour un vrai IBAN (ex: FR76 + 23 car)
        if len(match.group(0).replace(" ", "")) >= 14:
            violations.append(
                ViolationDetail(
                    category="banking_info",
                    description="Coordonnées bancaires / IBAN détectés",
                    matched_fragment=match.group(0),
                )
            )

    # E. Détection Phrases d'évasion de plateforme
    for phrase in BYPASS_PHRASES:
        if phrase in norm_content:
            violations.append(
                ViolationDetail(
                    category="platform_bypass_phrase",
                    description=f"Tentative explicite de contournement détectée ('{phrase}')",
                    matched_fragment=phrase,
                )
            )

    # F. Détection Langage inapproprié / Insultes
    words = re.findall(r'\b\w+\b', norm_content)
    for bad_word in INAPPROPRIATE_WORDS:
        if bad_word in words or any(bad_word in w for w in words if len(w) > 4 and bad_word in w):
            violations.append(
                ViolationDetail(
                    category="inappropriate_language",
                    description="Propos inappropriés ou insultes détectés",
                    matched_fragment=bad_word,
                )
            )

    return violations


def mask_sensitive_data(content: str) -> str:
    """Remplace les coordonnées et contenus sensibles par des balises masquées."""
    filtered = content

    # 1. Masquage des numéros de téléphone
    for rx in PHONE_REGEXES:
        filtered = rx.sub("[NUMÉRO MASQUÉ]", filtered)

    # 2. Masquage des adresses e-mail
    for rx in EMAIL_REGEXES:
        filtered = rx.sub("[EMAIL MASQUÉ]", filtered)

    # 3. Masquage des liens externes
    for rx in BYPASS_LINK_REGEXES:
        filtered = rx.sub("[LIEN EXTERNE MASQUÉ]", filtered)

    # 4. Masquage des IBAN
    filtered = IBAN_REGEX.sub("[IBAN MASQUÉ]", filtered)

    # 5. Masquage des insultes / mots inappropriés
    for bad_word in INAPPROPRIATE_WORDS:
        pattern = re.compile(re.escape(bad_word), re.IGNORECASE)
        filtered = pattern.sub("[CONTENU MODÉRÉ]", filtered)

    return filtered


def moderate_message(content: str, default_mode: str = "mask") -> ModerationResult:
    """
    Fonction principale de modération d'un message.
    
    Modes supportés :
    - 'mask' : Masque les coordonnées privées et contenus inappropriés.
    - 'block' : Rejette le message s'il comporte des violations critiques.
    - 'audit' : Flag uniquement sans altération de texte.
    """
    violations = detect_violations(content)
    is_flagged = len(violations) > 0
    reasons = sorted(list({v.category for v in violations}))

    # Décision de l'action selon la sévérité et le mode
    has_toxic_content = "inappropriate_language" in reasons
    
    # Si le mode demandé est 'block' ou si propos injurieux sévères, action = 'block'
    if default_mode == "block" and is_flagged:
        action = "block"
        filtered_content = content
    elif has_toxic_content:
        # Les propos injurieux peuvent être directement bloqués ou masqués
        action = "mask" if default_mode == "mask" else "block"
        filtered_content = mask_sensitive_data(content) if action == "mask" else content
    elif is_flagged:
        action = "mask" if default_mode == "mask" else "allow"
        filtered_content = mask_sensitive_data(content) if default_mode == "mask" else content
    else:
        action = "allow"
        filtered_content = content

    return ModerationResult(
        is_flagged=is_flagged,
        action=action,
        reasons=reasons,
        violations=violations,
        original_content=content,
        filtered_content=filtered_content,
    )


def check_text_moderation(content: str, default_mode: str = "mask") -> dict:
    """
    Helper pour la vérification et modération de texte retournant un dictionnaire standard.
    Utile pour l'intégration rapide dans les webhooks et scripts.
    """
    res = moderate_message(content, default_mode=default_mode)
    return {
        "is_safe": not res.is_flagged,
        "is_flagged": res.is_flagged,
        "action": res.action,
        "reasons": res.reasons,
        "violations": [v.model_dump() for v in res.violations],
        "original_content": res.original_content,
        "filtered_content": res.filtered_content,
    }


def check_content_safety(content: str, default_mode: str = "mask") -> dict:
    """
    Analyse de sécurité anti-désintermédiation et modération de contenu.
    Renvoie un dictionnaire avec is_safe, is_flagged, filtered_content, reasons, etc.
    """
    return check_text_moderation(content, default_mode=default_mode)


