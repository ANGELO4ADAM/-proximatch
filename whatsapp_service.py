"""
Module WhatsApp Service Premium pour ProxiMatch.

Gère la mise en forme avancée, les images de haute qualité, les messages interactifs
(boutons cliquables Quick Reply, Listes de sélection) et l'intégration avec Meta WhatsApp Cloud API.
"""

import os
import re
from datetime import datetime
from typing import Any, Optional
import httpx

# Configuration Meta WhatsApp Cloud API
WHATSAPP_TOKEN = os.getenv("WHATSAPP_TOKEN", "TON_TOKEN_PERMANENT_META")
VERIFY_TOKEN = os.getenv("VERIFY_TOKEN", os.getenv("WHATSAPP_VERIFY_TOKEN", "proximatch_secure_verify_token"))
PHONE_NUMBER_ID = os.getenv("PHONE_NUMBER_ID", "TON_PHONE_NUMBER_ID")
WHATSAPP_API_URL = os.getenv("WHATSAPP_API_URL", f"https://graph.facebook.com/v25.0/{PHONE_NUMBER_ID}/messages")


# Bannières & Images Haute Résolution par catégorie de service (Unsplash optimisées)
SERVICE_IMAGES = {
    "cleaning": "https://images.unsplash.com/photo-1581578731548-c64695cc6952?w=800&auto=format&fit=crop&q=80",
    "plumbing": "https://images.unsplash.com/photo-1585704032915-c3400ca199e7?w=800&auto=format&fit=crop&q=80",
    "gardening": "https://images.unsplash.com/photo-1558904541-efa8c4a08931?w=800&auto=format&fit=crop&q=80",
    "electrical": "https://images.unsplash.com/photo-1621905251189-08b45d6a269e?w=800&auto=format&fit=crop&q=80",
    "painting": "https://images.unsplash.com/photo-1589939705384-5185137a7f0f?w=800&auto=format&fit=crop&q=80",
    "moving": "https://images.unsplash.com/photo-1600585152220-90363fe7e115?w=800&auto=format&fit=crop&q=80",
    "confirmed": "https://images.unsplash.com/photo-1560518883-ce09059eeffa?w=800&auto=format&fit=crop&q=80",
    "welcome": "https://images.unsplash.com/photo-1521737711867-e3b97375f902?w=800&auto=format&fit=crop&q=80",
    "general": "https://images.unsplash.com/photo-1517048676732-d65bc937f952?w=800&auto=format&fit=crop&q=80",
    "security": "https://images.unsplash.com/photo-1563986768609-322da13575f3?w=800&auto=format&fit=crop&q=80",
}

# Journal en mémoire des messages WhatsApp envoyés (pour le simulateur et les tests)
SIMULATED_MESSAGES_LOG: list[dict[str, Any]] = []


def normalize_simple(text: str) -> str:
    """Supprime les accents et convertit en minuscules."""
    import unicodedata
    nfkd = unicodedata.normalize('NFKD', text or "")
    return "".join([c for c in nfkd if not unicodedata.combining(c)]).lower()


def get_service_image(category_or_title: str) -> str:
    """Retourne l'URL d'une image premium selon la compétence ou le titre de la mission."""
    txt = normalize_simple(category_or_title)
    if any(k in txt for k in ["demenag", "carton", "meuble", "manutention", "portage"]):
        return SERVICE_IMAGES["moving"]
    if any(k in txt for k in ["plomb", "fuite", "tuyau", "robinet", "sanitaire", "evier", "lavabo"]):
        return SERVICE_IMAGES["plumbing"]
    if any(k in txt for k in ["menage", "nettoy", "repass", "vitre", "propret", "sol"]):
        return SERVICE_IMAGES["cleaning"]
    if any(k in txt for k in ["jardin", "pelouse", "haie", "tonte", "arrosage", "espace vert"]):
        return SERVICE_IMAGES["gardening"]
    if any(k in txt for k in ["electr", "tableau", "cable", "prise", "disjoncteur"]):
        return SERVICE_IMAGES["electrical"]
    if any(k in txt for k in ["peint", "enduit", "poncage", "peintre"]):
        return SERVICE_IMAGES["painting"]
    if any(k in txt for k in ["confirm", "accept", "succes", "assign"]):
        return SERVICE_IMAGES["confirmed"]
    if any(k in txt for k in ["securit", "moderat", "bloqu"]):
        return SERVICE_IMAGES["security"]
    return SERVICE_IMAGES["general"]


def build_premium_match_interactive(
    recipient_phone: str,
    request_id: int,
    request_title: str,
    provider_name: str,
    match_score: float,
    hourly_rate: float,
    location: str,
    skills: str = "",
    image_url: Optional[str] = None,
) -> dict[str, Any]:
    """
    Construit un message WhatsApp interactif Premium avec image d'en-tête,
    mise en forme avancée et 3 boutons d'action cliquables.
    """
    img = image_url or get_service_image(f"{request_title} {skills}")

    body_text = (
        f"✨ *PROXIMATCH • NOUVEAU MATCH TROUVÉ* ✨\n\n"
        f"Nous avons identifié le prestataire idéal pour votre demande :\n"
        f"📌 *{request_title}*\n\n"
        f"👤 *Prestataire :* {provider_name}\n"
        f"⭐ *Score de compatibilité :* {match_score}%\n"
        f"💰 *Tarif horaire :* {hourly_rate:.2f} € / h\n"
        f"📍 *Zone :* {location}\n\n"
        f"> 🛡️ _Prestataire vérifié • Assurance ProxiMatch incluse._\n\n"
        f"Que souhaitez-vous faire ?"
    )

    return {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": recipient_phone,
        "type": "interactive",
        "interactive": {
            "type": "button",
            "header": {
                "type": "image",
                "image": {"link": img}
            },
            "body": {
                "text": body_text
            },
            "footer": {
                "text": "⚡ ProxiMatch • Réservation instantanée"
            },
            "action": {
                "buttons": [
                    {
                        "type": "reply",
                        "reply": {
                            "id": f"accept_req_{request_id}",
                            "title": "✅ Accepter le profil"
                        }
                    },
                    {
                        "type": "reply",
                        "reply": {
                            "id": f"details_req_{request_id}",
                            "title": "📋 Voir les détails"
                        }
                    },
                    {
                        "type": "reply",
                        "reply": {
                            "id": f"decline_req_{request_id}",
                            "title": "🔄 Autre profil"
                        }
                    }
                ]
            }
        }
    }


def build_premium_confirmation_interactive(
    recipient_phone: str,
    request_id: int,
    request_title: str,
    provider_name: str,
    location: str,
    hourly_rate: float,
    duration_hours: float = 2.0,
    service_date: Optional[str] = None,
) -> dict[str, Any]:
    """
    Construit un message interactif Premium de confirmation de mission avec récapitulatif
    financier, checklist et boutons cliquables.
    """
    img = SERVICE_IMAGES["confirmed"]
    total_est = hourly_rate * duration_hours
    date_display = service_date or "À convenir avec le prestataire"

    body_text = (
        f"🎉 *MISSION CONFIRMÉE & ASSIGNÉE !* 🎉\n\n"
        f"Votre mission a été validée avec succès auprès de *{provider_name}*.\n\n"
        f"📋 *RÉCAPITULATIF DE LA MISSION #{request_id}* :\n"
        f"• *Prestation :* {request_title}\n"
        f"• *Artisan / Prestataire :* {provider_name}\n"
        f"• *Lieu d'intervention :* {location}\n"
        f"• *Date & Heure :* {date_display}\n"
        f"• *Durée estimée :* {duration_hours}h\n"
        f"• *Estimation totale :* ~{total_est:.2f} € ({hourly_rate:.2f}€/h)\n\n"
        f"> 🔒 _Le paiement sécurisé sera débloqué après réalisation de la prestation._"
    )

    return {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": recipient_phone,
        "type": "interactive",
        "interactive": {
            "type": "button",
            "header": {
                "type": "image",
                "image": {"link": img}
            },
            "body": {
                "text": body_text
            },
            "footer": {
                "text": "🛡️ ProxiMatch Garantie Sérénité"
            },
            "action": {
                "buttons": [
                    {
                        "type": "reply",
                        "reply": {
                            "id": f"details_req_{request_id}",
                            "title": "📋 Revoir les détails"
                        }
                    },
                    {
                        "type": "reply",
                        "reply": {
                            "id": "contact_support",
                            "title": "💬 Assistance 24/7"
                        }
                    }
                ]
            }
        }
    }


def build_premium_details_interactive(
    recipient_phone: str,
    request_id: int,
    request_title: str,
    description: str,
    location: str,
    max_budget: float,
    duration_hours: float,
    service_date: str,
) -> dict[str, Any]:
    """Construit un message interactif détaillant une demande."""
    img = get_service_image(request_title)

    body_text = (
        f"📋 *DÉTAILS COMPLETS DE LA DEMANDE #{request_id}*\n\n"
        f"🔹 *Titre :* {request_title}\n"
        f"🔹 *Description :* {description or 'Non spécifiée'}\n"
        f"🔹 *Lieu :* {location}\n"
        f"🔹 *Budget max alloué :* {max_budget:.2f} €\n"
        f"🔹 *Durée prévue :* {duration_hours} h\n"
        f"🔹 *Date souhaitée :* {service_date}\n\n"
        f"> ⚡ _Les prestataires disponibles dans cette zone ont été notifiés._"
    )

    return {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": recipient_phone,
        "type": "interactive",
        "interactive": {
            "type": "button",
            "header": {
                "type": "image",
                "image": {"link": img}
            },
            "body": {
                "text": body_text
            },
            "footer": {
                "text": "⚡ ProxiMatch Direct"
            },
            "action": {
                "buttons": [
                    {
                        "type": "reply",
                        "reply": {
                            "id": f"accept_req_{request_id}",
                            "title": "✅ Confirmer mission"
                        }
                    },
                    {
                        "type": "reply",
                        "reply": {
                            "id": f"decline_req_{request_id}",
                            "title": "❌ Annuler la demande"
                        }
                    }
                ]
            }
        }
    }


def build_premium_security_alert_interactive(
    recipient_phone: str,
    reason: str = "Contournement ou langage inapproprié",
) -> dict[str, Any]:
    """Construit un message interactif de sécurité & modération premium."""
    img = SERVICE_IMAGES["security"]

    body_text = (
        f"⚠️ *AVERTISSEMENT DE SÉCURITÉ PROXIMATCH* ⚠️\n\n"
        f"Votre message a été filtré pour le motif suivant :\n"
        f"🛑 *{reason}*\n\n"
        f"Pour votre sécurité, les coordonnées privées (téléphone, e-mail, liens externes) "
        f"et les paiements directs hors plateforme sont strictement encadrés afin de garantir l'assurance et la protection des deux parties.\n\n"
        f"> 🛡️ _Veuillez poursuivre vos échanges via la messagerie sécurisée ProxiMatch._"
    )

    return {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": recipient_phone,
        "type": "interactive",
        "interactive": {
            "type": "button",
            "header": {
                "type": "image",
                "image": {"link": img}
            },
            "body": {
                "text": body_text
            },
            "footer": {
                "text": "🛡️ ProxiMatch Sécurité & Confiance"
            },
            "action": {
                "buttons": [
                    {
                        "type": "reply",
                        "reply": {
                            "id": "view_guidelines",
                            "title": "📜 Voir la charte"
                        }
                    },
                    {
                        "type": "reply",
                        "reply": {
                            "id": "contact_support",
                            "title": "💬 Contacter support"
                        }
                    }
                ]
            }
        }
    }


def build_premium_text_payload(recipient_phone: str, text_message: str) -> dict[str, Any]:
    """Construit un payload WhatsApp texte standard optimisé."""
    return {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": recipient_phone,
        "type": "text",
        "text": {"preview_url": True, "body": text_message},
    }


async def send_whatsapp_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """
    Envoie le payload JSON à l'API Meta WhatsApp Cloud.
    Si non configuré (mode dev/test), enregistre le message dans le journal de simulation.
    """
    recipient = payload.get("to", "inconnu")
    msg_type = payload.get("type", "text")

    # Enregistrement dans le journal de simulation pour le Dashboard & Tests
    log_entry = {
        "timestamp": datetime.now().isoformat(),
        "recipient": recipient,
        "type": msg_type,
        "payload": payload,
    }
    SIMULATED_MESSAGES_LOG.append(log_entry)
    # Limiter la taille du log à 100 éléments
    if len(SIMULATED_MESSAGES_LOG) > 100:
        SIMULATED_MESSAGES_LOG.pop(0)

    # Si le token Meta n'est pas configuré, mode simulation
    if not WHATSAPP_TOKEN or WHATSAPP_TOKEN == "TON_TOKEN_PERMANENT_META":
        print(f"[Simulation WhatsApp Premium] Type={msg_type} vers {recipient}")
        if msg_type == "interactive":
            interactive_data = payload.get("interactive", {})
            header = interactive_data.get("header", {})
            body = interactive_data.get("body", {}).get("text", "")
            buttons = [b["reply"]["title"] for b in interactive_data.get("action", {}).get("buttons", [])]
            print(f"  🖼️ Image Header: {header.get('image', {}).get('link', 'Aucune')}")
            print(f"  📝 Body:\n{body}")
            print(f"  🔘 Boutons: {', '.join(buttons)}")
        else:
            print(f"  📝 Texte: {payload.get('text', {}).get('body', '')}")
        return {"status": "simulated", "recipient": recipient, "type": msg_type}

    # Envoi réel via l'API Meta Graph API
    url = f"https://graph.facebook.com/v25.0/{PHONE_NUMBER_ID}/messages"
    headers = {
        "Authorization": f"Bearer {WHATSAPP_TOKEN}",
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(url, headers=headers, json=payload, timeout=10.0)
            print(f"[Meta WhatsApp Response] Code: {response.status_code}")
            if response.status_code >= 400:
                print(f"[Meta WhatsApp Error]: {response.text}")
                # Fallback : si l'envoi interactif échoue auprès de Meta, tenter un fallback texte simple
                if msg_type == "interactive":
                    fallback_text = payload.get("interactive", {}).get("body", {}).get("text", "")
                    fallback_payload = build_premium_text_payload(recipient, fallback_text)
                    await client.post(url, headers=headers, json=fallback_payload, timeout=5.0)
            return {"status": "sent", "http_code": response.status_code, "response": response.text}
        except Exception as e:
            print(f"[Meta WhatsApp Exception]: {e}")
            return {"status": "error", "error": str(e)}


async def send_whatsapp_reply(recipient_phone: str, text_message: str) -> dict[str, Any]:
    """Helper rétrocompatible pour envoyer du texte formaté."""
    payload = build_premium_text_payload(recipient_phone, text_message)
    return await send_whatsapp_payload(payload)


send_whatsapp_text_message = send_whatsapp_reply



async def send_whatsapp_interactive_match(
    recipient_phone: str,
    request_id: int,
    request_title: str,
    provider_name: str,
    match_score: float,
    hourly_rate: float,
    location: str,
    skills: str = "",
) -> dict[str, Any]:
    """Helper direct pour envoyer une proposition de match Premium."""
    payload = build_premium_match_interactive(
        recipient_phone=recipient_phone,
        request_id=request_id,
        request_title=request_title,
        provider_name=provider_name,
        match_score=match_score,
        hourly_rate=hourly_rate,
        location=location,
        skills=skills,
    )
    return await send_whatsapp_payload(payload)


async def send_whatsapp_interactive_confirmation(
    recipient_phone: str,
    request_id: int,
    request_title: str,
    provider_name: str,
    location: str,
    hourly_rate: float,
    duration_hours: float = 2.0,
    service_date: Optional[str] = None,
) -> dict[str, Any]:
    """Helper direct pour envoyer une confirmation de réservation Premium."""
    payload = build_premium_confirmation_interactive(
        recipient_phone=recipient_phone,
        request_id=request_id,
        request_title=request_title,
        provider_name=provider_name,
        location=location,
        hourly_rate=hourly_rate,
        duration_hours=duration_hours,
        service_date=service_date,
    )
    return await send_whatsapp_payload(payload)


def send_whatsapp_interactive_message(
    recipient_phone: str,
    header_text: str,
    provider_name: str = "Prestataire Certifié",
    provider_phone: str = "06 ** ** ** 01",
    body_text: str = "",
    request_id: Optional[int] = None,
    image_url: Optional[str] = None,
) -> Optional[dict[str, Any]]:
    """
    Envoie un message WhatsApp interactif avec des boutons cliquables via l'API Cloud de Meta.
    """
    img_link = image_url or get_service_image(header_text)
    btn_accept_id = f"accept_req_{request_id}" if request_id else "btn_accept"
    btn_other_id = f"decline_req_{request_id}" if request_id else "btn_other"
    body_prefix = f"{body_text}\n" if body_text else ""

    payload = {
        "messaging_product": "whatsapp",
        "to": recipient_phone,
        "type": "interactive",
        "interactive": {
            "type": "button",
            "header": {
                "type": "image" if img_link else "text",
                **({"image": {"link": img_link}} if img_link else {"text": "✨ ProxiMatch - Matching Validé"})
            },
            "body": {
                "text": f"*{header_text}*\n\n"
                        f"{body_prefix}"
                        f"🏆 *Meilleur profil :* {provider_name}\n"
                        f"📞 *Contact direct :* {provider_phone}\n\n"
                        f"Souhaitez-vous valider cette mise en relation ?"
            },
            "footer": {
                "text": "Service automatisé par IA • ProxiMatch"
            },
            "action": {
                "buttons": [
                    {
                        "type": "reply",
                        "reply": {
                            "id": btn_accept_id,
                            "title": "✅ Oui, valider"
                        }
                    },
                    {
                        "type": "reply",
                        "reply": {
                            "id": btn_other_id,
                            "title": "🔄 Autre profil"
                        }
                    }
                ]
            }
        }
    }

    # Enregistrement pour le suivi et la simulation Dashboard / Tests
    log_entry = {
        "timestamp": datetime.now().isoformat(),
        "recipient": recipient_phone,
        "type": "interactive",
        "payload": payload,
    }
    SIMULATED_MESSAGES_LOG.append(log_entry)
    if len(SIMULATED_MESSAGES_LOG) > 100:
        SIMULATED_MESSAGES_LOG.pop(0)

    if not recipient_phone or WHATSAPP_TOKEN == "TON_TOKEN_PERMANENT_META":
        print(f"[Simulation WhatsApp Interactif] Envoyé à {recipient_phone}: {provider_name} ({provider_phone})")
        return {"status": "simulated", "payload": payload}

    headers = {
        "Authorization": f"Bearer {WHATSAPP_TOKEN}",
        "Content-Type": "application/json",
    }

    try:
        response = httpx.post(WHATSAPP_API_URL, headers=headers, json=payload, timeout=10.0)
        response_data = response.json()
        print(f"Message WhatsApp interactif envoyé à {recipient_phone}: {response_data}")
        return response_data
    except Exception as e:
        print(f"Erreur lors de l'envoi du message interactif : {e}")
        return None


def send_whatsapp_message(recipient_phone: str, text_message: str) -> Optional[dict[str, Any]]:
    """Envoie un message WhatsApp texte sortant via Meta Cloud API."""
    payload = build_premium_text_payload(recipient_phone, text_message)
    log_entry = {
        "timestamp": datetime.now().isoformat(),
        "recipient": recipient_phone,
        "type": "text",
        "payload": payload,
    }
    SIMULATED_MESSAGES_LOG.append(log_entry)
    if len(SIMULATED_MESSAGES_LOG) > 100:
        SIMULATED_MESSAGES_LOG.pop(0)

    if not recipient_phone or WHATSAPP_TOKEN == "TON_TOKEN_PERMANENT_META":
        print(f"[Simulation WhatsApp] Message à {recipient_phone} : {text_message}")
        return {"status": "simulated", "payload": payload}

    headers = {
        "Authorization": f"Bearer {WHATSAPP_TOKEN}",
        "Content-Type": "application/json",
    }
    try:
        response = httpx.post(WHATSAPP_API_URL, headers=headers, json=payload, timeout=10.0)
        return response.json()
    except Exception as e:
        print(f"Erreur lors de l'envoi du message WhatsApp : {e}")
        return None


