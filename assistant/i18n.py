"""
English / Spanish for the assistant.

t(lang, key, **kw)    UI strings.
item_text(item, lang) Subject + snippet for items the assistant generates
                      (Moveware, ClickUp, WhatsApp), rebuilt from item meta
                      so they follow the user's language. Mail keeps its
                      original text. Falls back to the stored text when an
                      older row lacks the meta fields.
"""

LANGS = ("en", "es")

S = {
    # nav / shell
    "nav_admin": ("Admin", "Admin"),
    "nav_dashboard": ("My dashboard", "Mi tablero"),
    "nav_library": ("← Automation Library", "← Biblioteca de automatizaciones"),
    "title_suffix": ("Thelsa AI Assistant", "Asistente de IA Thelsa"),
    "paused_title": ("Access paused", "Acceso pausado"),
    "paused_body": ("Your assistant has been turned off by an administrator.",
                    "Un administrador desactivó tu asistente."),
    "form_expired": ("Form expired — reload the page and try again.",
                     "El formulario expiró. Recarga la página e inténtalo de nuevo."),
    # greeting
    "good_morning": ("Good morning", "Buenos días"),
    "good_afternoon": ("Good afternoon", "Buenas tardes"),
    "good_evening": ("Good evening", "Buenas noches"),
    "dash_sub": ("Everything that needs you, in order of urgency.",
                 "Todo lo que necesita de ti, ordenado por urgencia."),
    # banners
    "almost_ready": ("Your assistant is almost ready.", "Tu asistente está casi listo."),
    "almost_ready_sub": ("The administrator is finishing setup. Email connection will be available shortly.",
                         "El administrador está terminando la configuración. Pronto podrás conectar tu correo."),
    "connect_title": ("Connect your Thelsa mailbox", "Conecta tu correo de Thelsa"),
    "connect_sub": ("So your assistant can check your email 5 times a day, even when you're not here.",
                    "Para que tu asistente revise tu correo 5 veces al día, aunque no estés conectado."),
    "connect_btn": ("Connect mailbox", "Conectar correo"),
    "reconnect_title": ("Your mailbox needs to be reconnected.", "Hay que volver a conectar tu correo."),
    "reconnect_btn": ("Reconnect", "Reconectar"),
    "refreshing": ("Refreshing your dashboard… this page will reload in a few seconds.",
                   "Actualizando tu tablero… esta página se recargará en unos segundos."),
    # KPIs / tiers
    "urgent": ("Urgent", "Urgente"),
    "today": ("Today", "Hoy"),
    "soon": ("Soon", "Pronto"),
    "kpi_moveware": ("Moveware to-dos", "Pendientes Moveware"),
    "kpi_clickup": ("TIM shipments", "Embarques TIM"),
    "kpi_mail": ("Emails", "Correos"),
    # freshness
    "fresh_mail": ("Mail", "Correo"),
    "fresh_next": ("Next automatic refresh", "Próxima actualización automática"),
    "set_up": ("set up", "configurar"),
    "never": ("never", "nunca"),
    "not_connected": ("not connected", "no conectado"),
    "tomorrow_at": ("tomorrow {time}", "mañana {time}"),
    # filters / actions
    "all": ("All", "Todo"),
    "refresh_now": ("↻ Refresh now", "↻ Actualizar ahora"),
    "empty": ("Nothing needs you right now. 🎉", "No hay pendientes por ahora. 🎉"),
    "open": ("Open ↗", "Abrir ↗"),
    "suggest_reply": ("✎ Suggest reply", "✎ Sugerir respuesta"),
    "done": ("✓ Done", "✓ Hecho"),
    "undo_done": ("Undo done", "Deshacer"),
    "urgency_title": ("urgency {score}/100", "urgencia {score}/100"),
    "side_pack": ("Pack", "Empaque"),
    "side_since": ("Since", "Desde"),
    "side_last_step": ("Last step", "Último paso"),
    # drafts
    "draft_saved": ("✓ Saved to your Outlook Drafts", "✓ Guardado en tus Borradores de Outlook"),
    "draft_save": ("Save to Outlook Drafts", "Guardar en Borradores de Outlook"),
    "draft_copy_hint": ("Copy this into your reply in Outlook. (Saving to Drafts needs IT to enable it.)",
                        "Copia esto en tu respuesta en Outlook. (Guardar en Borradores requiere que TI lo habilite.)"),
    "copy": ("Copy", "Copiar"),
    "copied": ("Copied", "Copiado"),
    "draft_error": ("(Couldn't generate a suggestion right now: {err})",
                    "(No se pudo generar una sugerencia en este momento: {err})"),
    "draft_not_saved": ("[Not saved to Outlook: {err}]", "[No se guardó en Outlook: {err}]"),
    # footer
    "foot_private": ("Only you can see this dashboard. Emails are scanned from your own mailbox.",
                     "Solo tú puedes ver este tablero. Los correos se revisan desde tu propio buzón."),
    "foot_moveware": ("Moveware to-dos come from files where you are the coordinator.",
                      "Los pendientes de Moveware vienen de los expedientes donde eres coordinador(a)."),
    "foot_clickup_all": ("ClickUp items are the TIM shipments you own.",
                         "Los pendientes de ClickUp son los embarques TIM a tu cargo."),
    "foot_clickup_assigned": ("ClickUp items are TIM shipments assigned to you.",
                              "Los pendientes de ClickUp son los embarques TIM asignados a ti."),
    "disconnect_mailbox": ("Disconnect mailbox", "Desconectar correo"),
    "disconnect_confirm": ("Disconnect your mailbox and delete its data from the assistant?",
                           "¿Desconectar tu correo y borrar sus datos del asistente?"),
    "add_whatsapp": ("Add WhatsApp (beta)", "Agregar WhatsApp (beta)"),
    # consent
    "consent_title": ("Your AI Assistant", "Tu Asistente de IA"),
    "consent_sub": ("Before we set up your personal dashboard, here's exactly what it does.",
                    "Antes de preparar tu tablero personal, esto es exactamente lo que hace."),
    "consent_reads": ("<b>What it reads:</b> the sender, subject and first lines of emails in your Thelsa inbox "
                      "from the last 7 days, plus your Moveware and ClickUp files if you have any.",
                      "<b>Qué lee:</b> el remitente, el asunto y las primeras líneas de los correos de tu bandeja de "
                      "Thelsa de los últimos 7 días, y tus expedientes de Moveware y ClickUp si los tienes."),
    "consent_often": ("<b>How often:</b> automatically at 6am, 9am, 12pm, 3pm and 6pm (Mexico City), and whenever "
                      "you press \"Refresh now\".",
                      "<b>Con qué frecuencia:</b> automáticamente a las 6, 9, 12, 15 y 18 h (hora de CDMX), y cada "
                      "vez que presiones \"Actualizar ahora\"."),
    "consent_stores": ("<b>What it stores:</b> only the short list shown on your dashboard. Your mailbox sign-in is "
                       "stored encrypted so it can refresh while you're away. Full emails are never stored.",
                       "<b>Qué guarda:</b> solo la lista breve que ves en tu tablero. El acceso a tu correo se guarda "
                       "cifrado para poder actualizar aunque no estés. Nunca guarda correos completos."),
    "consent_drafts": ("<b>Drafts:</b> it can suggest replies and save them to your Outlook Drafts. It never sends "
                       "anything.",
                       "<b>Borradores:</b> puede sugerir respuestas y guardarlas en tus Borradores de Outlook. Nunca "
                       "envía nada."),
    "consent_privacy": ("<b>Privacy:</b> only you can see your dashboard.",
                        "<b>Privacidad:</b> solo tú puedes ver tu tablero."),
    "consent_stop": ("<b>Stop anytime:</b> \"Disconnect\" deletes your stored sign-in and everything derived from "
                     "it immediately.",
                     "<b>Puedes detenerlo cuando quieras:</b> \"Desconectar\" borra de inmediato tu acceso guardado y "
                     "todo lo que se obtuvo con él."),
    "consent_agree": ("I agree to the assistant reading my mailbox and my Moveware / ClickUp files as described above.",
                      "Acepto que el asistente lea mi correo y mis expedientes de Moveware / ClickUp como se describe arriba."),
    "consent_whatsapp": ("<b>Optional (beta):</b> also show unread WhatsApp chats, using a browser extension that "
                         "reads the chat list only while WhatsApp Web is open in my browser. Chat names and the "
                         "last-message preview are sent; full history is not.",
                         "<b>Opcional (beta):</b> mostrar también los chats de WhatsApp sin leer, con una extensión "
                         "del navegador que lee la lista de chats solo mientras WhatsApp Web está abierto en mi "
                         "navegador. Se envían los nombres de los chats y la vista previa del último mensaje; nunca "
                         "el historial completo."),
    "continue": ("Continue", "Continuar"),
    "get_started": ("Get started", "Comenzar"),
    # WhatsApp page
    "wa_intro": ("Shows your unread WhatsApp chats on your dashboard. It works through a small Chrome extension "
                 "that reads your chat list <b>only while WhatsApp Web is open</b> in your browser, so the WhatsApp "
                 "section shows \"as of\" the last time it synced.",
                 "Muestra tus chats de WhatsApp sin leer en tu tablero. Funciona con una pequeña extensión de Chrome "
                 "que lee tu lista de chats <b>solo mientras WhatsApp Web está abierto</b> en tu navegador, así que la "
                 "sección de WhatsApp muestra la hora de la última sincronización."),
    "wa_agree": ("I agree to send my WhatsApp chat names, unread counts and last-message previews to my private "
                 "dashboard. Full history is never read.",
                 "Acepto enviar a mi tablero privado los nombres de mis chats de WhatsApp, el número de mensajes sin "
                 "leer y la vista previa del último mensaje. Nunca se lee el historial completo."),
    "wa_turn_on": ("Turn on WhatsApp", "Activar WhatsApp"),
    "wa_step1": ("<b>1.</b> <a href=\"/assistant/whatsapp/extension.zip\">Download the extension</a> and unzip it.",
                 "<b>1.</b> <a href=\"/assistant/whatsapp/extension.zip\">Descarga la extensión</a> y descomprímela."),
    "wa_step2": ("<b>2.</b> In Chrome open <code>chrome://extensions</code>, turn on <b>Developer mode</b>, click "
                 "<b>Load unpacked</b> and choose the unzipped folder.",
                 "<b>2.</b> En Chrome abre <code>chrome://extensions</code>, activa el <b>Modo de desarrollador</b>, "
                 "da clic en <b>Cargar descomprimida</b> y elige la carpeta descomprimida."),
    "wa_step3": ("<b>3.</b> Click the extension's icon and paste your sync key.",
                 "<b>3.</b> Da clic en el ícono de la extensión y pega tu clave de sincronización."),
    "wa_step4": ("<b>4.</b> Open <a href=\"https://web.whatsapp.com\" target=\"_blank\" rel=\"noopener\">"
                 "web.whatsapp.com</a>. It syncs every 5 minutes while open.",
                 "<b>4.</b> Abre <a href=\"https://web.whatsapp.com\" target=\"_blank\" rel=\"noopener\">"
                 "web.whatsapp.com</a>. Se sincroniza cada 5 minutos mientras está abierto."),
    "wa_key": ("<b>Your sync key</b> (shown once — copy it now):",
               "<b>Tu clave de sincronización</b> (solo se muestra una vez, cópiala ahora):"),
    "wa_last": ("Last sync", "Última sincronización"),
    "wa_new_key": ("Create a new sync key", "Crear una nueva clave"),
    "wa_first_key": ("Create my sync key", "Crear mi clave"),
    "wa_off": ("Turn off WhatsApp &amp; delete its data", "Desactivar WhatsApp y borrar sus datos"),
}

KIND = {
    "needs_reply": ("Needs reply", "Por responder"),
    "flagged": ("Flagged", "Marcado"),
    "whatsapp": ("WhatsApp", "WhatsApp"),
    "invoice_file": ("Invoice file", "Facturar"),
    "invoice_charge": ("Invoice charges", "Facturar cargos"),
    "upload_docs": ("Send documents", "Enviar documentos"),
    "request_docs": ("Request documents", "Solicitar documentos"),
    "tim_docs": ("Documents pending", "Documentos pendientes"),
    "tim_stalled": ("Stalled", "Detenido"),
    "tim_step": ("Next step", "Siguiente paso"),
}
SOURCE = {
    "microsoft": ("Mail", "Correo"),
    "moveware": ("Moveware (TMS)", "Moveware (TMS)"),
    "clickup": ("ClickUp (TIM)", "ClickUp (TIM)"),
    "whatsapp": ("WhatsApp", "WhatsApp"),
}
STAGE = {
    "booked": ("Booked", "Reservado"),
    "docs_pending": ("Documents pending", "Documentos pendientes"),
    "green_light": ("Green light", "Luz verde"),
    "in_transit_to_border": ("To border", "Hacia la frontera"),
    "customs_clearance": ("Customs", "Aduana"),
    "at_hub": ("At Monterrey hub", "En bodega Monterrey"),
    "onward_leg": ("Onward leg", "Tramo final"),
    "out_for_delivery": ("Out for delivery", "En entrega"),
    "delivered": ("Delivered", "Entregado"),
    "closed": ("Closed", "Cerrado"),
    "unknown": ("In progress", "En proceso"),
}
MONTHS_ES = ("ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic")


def norm(lang) -> str:
    return lang if lang in LANGS else "en"


def _pick(pair, lang):
    return pair[1] if norm(lang) == "es" else pair[0]


def t(lang, key, **kw) -> str:
    pair = S.get(key)
    if not pair:
        return key
    s = _pick(pair, lang)
    return s.format(**kw) if kw else s


def kind_label(kind, lang):
    return _pick(KIND.get(kind, (kind, kind)), lang)


def source_label(source, lang):
    return _pick(SOURCE.get(source, (source, source)), lang)


def stage_label(stage, lang):
    return _pick(STAGE.get(stage or "unknown", STAGE["unknown"]), lang)


def month_abbr(d, lang):
    return MONTHS_ES[d.month - 1] if norm(lang) == "es" else d.strftime("%b")


def _money(v) -> str:
    return "$" + f"{float(v or 0):,.0f}"


def item_text(item, lang):
    """(subject, snippet) in the user's language for generated items."""
    es = norm(lang) == "es"
    kind, m = item.get("kind"), item.get("meta") or {}
    subj, snip = item.get("subject") or "", item.get("snippet") or ""
    try:
        if kind == "invoice_file" and "job" in m and "days" in m:
            client = m.get("client") or item.get("from_name") or ""
            val = m.get("value")
            if es:
                why = "entregada" if m.get("embassy") else "empacada"
                subj = f"Facturar expediente {m['job']} — {client}"
                snip = (f"Mudanza {why} hace {m['days']} día(s) y aún sin facturar"
                        + (f" · {_money(val)} por facturar" if val else "")
                        + (" · Embajada/Consulado de EE. UU. (se factura después de la entrega)"
                           if m.get("embassy") else ""))
            else:
                why = "delivered" if m.get("embassy") else "packed"
                subj = f"Invoice file {m['job']} — {client}"
                snip = (f"Move {why} {m['days']} day(s) ago and not invoiced yet"
                        + (f" · {_money(val)} to bill" if val else "")
                        + (" · US Embassy/Consulate (bill after delivery)" if m.get("embassy") else ""))
        elif kind == "upload_docs" and "job" in m and "days" in m:
            client = m.get("client") or item.get("from_name") or ""
            if es:
                subj = f"Enviar ticket de peso y lista de empaque — {m['job']} {client}"
                snip = (f"Empacada hace {m['days']} día(s); sin peso real en Moveware. Descarga de SIT el "
                        "ticket de peso certificado y la lista de empaque y súbelos.")
            else:
                subj = f"Send weight ticket & packing list — {m['job']} {client}"
                snip = (f"Packed {m['days']} day(s) ago; no actual weight in Moveware. Download the certified "
                        "weight ticket + packing list from SIT and upload them.")
        elif kind == "request_docs" and "job" in m and "days_left" in m:
            client = m.get("client") or item.get("from_name") or ""
            if es:
                subj = f"Solicitar inventario valorado / formato de seguro — {m['job']} {client}"
                snip = f"Empaque en {m['days_left']} día(s) y sin valor declarado ni seguro en el expediente."
            else:
                subj = f"Request valued inventory / insurance form — {m['job']} {client}"
                snip = f"Pack in {m['days_left']} day(s) and no declared value or insurance on file."
        elif kind == "invoice_charge" and "job" in m and "approved" in m:
            a, i, g = _money(m["approved"]), _money(m.get("invoiced")), _money(m.get("value"))
            if es:
                subj = f"Facturar cargos adicionales aprobados — {m['job']}"
                snip = f"{a} aprobados por correo, {i} facturados ({g} sin facturar)"
            else:
                subj = f"Invoice approved extra charges — {m['job']}"
                snip = f"{a} approved by email, {i} invoiced ({g} not billed)"
        elif kind in ("tim_docs", "tim_stalled", "tim_step") and "step" in m and "total" in m:
            cust, ref = m.get("customer") or item.get("from_name") or "", m.get("ref") or ""
            subj = f"{m['step']} — {cust}{f' ({ref})' if ref else ''}"
            days = m.get("days")
            if es:
                idle = f" · sin avance en {days} día(s)" if days is not None else ""
                snip = f"{stage_label(m.get('stage'), 'es')} · {m.get('done', 0)}/{m['total']} pasos{idle}"
            else:
                idle = f" · no progress for {days} day(s)" if days is not None else ""
                snip = f"{stage_label(m.get('stage'), 'en')} · {m.get('done', 0)}/{m['total']} steps{idle}"
        elif kind == "whatsapp" and "unread" in m:
            n, name = int(m["unread"]), item.get("from_name") or ""
            if es:
                subj = f"{n} mensaje{'s' if n != 1 else ''} sin leer de {name}"
            else:
                subj = f"{n} unread message{'s' if n != 1 else ''} from {name}"
    except (KeyError, TypeError, ValueError):
        pass
    return subj, snip
