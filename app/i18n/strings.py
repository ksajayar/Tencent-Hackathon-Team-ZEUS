GEMINI_DEGRADED = {
    "en": "I'm having trouble thinking right now. Please try again in a moment.",
    "zh-Hans": "我现在有点想不清楚，请稍后再试。",
}

CONNECT_GOOGLE_LINK = {
    "en": "Tap this link to connect your Google account: {link}",
    "zh-Hans": "点击这个链接连接您的谷歌账号：{link}",
}

OAUTH_SUCCESS = {
    "en": "Your Google account is connected! I can now check your email and calendar.",
    "zh-Hans": "您的谷歌账号已连接！我现在可以查看您的邮件和日历了。",
}

OAUTH_DENIED = {
    "en": "No problem. You can connect your email later.",
    "zh-Hans": "没关系，您可以稍后再连接邮箱。",
}

OAUTH_ERROR = {
    "en": "Something went wrong connecting your Google account. Please try again by saying "
    "'connect google'.",
    "zh-Hans": "连接谷歌账号时出了点问题，请再说一次「连接谷歌」试试。",
}

RESCHEDULED_EVENT = {
    "en": "Your appointment '{summary}' has moved to {when}.",
    "zh-Hans": "您的日程「{summary}」改到了{when}。",
}

MEDICATION_REMINDER = {
    "en": "It is time for your {time_of_day} medicine, {name}. Please take {dose_text}.",
    "zh-Hans": "现在是您{time_of_day}吃{name}的时间。请服用{dose_text}。",
}

MEDICATION_GUARD_FALLBACK = {
    "en": "It is time for your medicine. Please check with your caregiver if unsure.",
    "zh-Hans": "现在是吃药的时间。如果不确定，请联系您的照顾者。",
}

# §17 safety walkthrough, fix B: MEDICATION_GUARD_FALLBACK above tells the
# recipient to "check with your caregiver" - circular and nonsensical when
# the recipient IS the caregiver. Used anywhere a medication-related guard
# fires on caregiver-facing text (_caregiver_qa, _caregiver_medication_query).
MEDICATION_GUARD_FALLBACK_CAREGIVER = {
    "en": "I can't confirm that safely right now. Please check {patient_name}'s "
    "medication record directly, or confirm with the doctor.",
    "zh-Hans": "我现在没办法安全确认这一点。请直接查看{patient_name}的用药记录，或向医生确认。",
}

MEDICATION_ACK_CONFIRMATION = {
    "en": "Good, thank you for letting me know.",
    "zh-Hans": "好的，谢谢您告诉我。",
}

NO_MEDICATIONS = {
    "en": "I don't have any medicines on file for you yet. Please check with your caregiver.",
    "zh-Hans": "我这里还没有您的药物记录，请联系您的照顾者。",
}

# Same reasoning as MEDICATION_GUARD_FALLBACK_CAREGIVER above - the patient
# version's "check with your caregiver" is circular for a caregiver asking
# on their own behalf. Used by _caregiver_medication_query.
NO_MEDICATIONS_CAREGIVER = {
    "en": "There are no medicines on file for {patient_name} yet.",
    "zh-Hans": "目前还没有{patient_name}的用药记录。",
}

# "What's my next appointment?" - docs/11 §11.7 demo step 3. Deterministic,
# no LLM in the answer path, same reasoning as NO_MEDICATIONS above: the
# events are stored facts, and the day/time phrasing is already rendered by
# calendar_service.render_when() in the persona's own register.
NEXT_APPOINTMENT = {
    "en": "Your next appointment is {summary}, {when}. You are all set.",
    "zh-Hans": "您的下一个预约是{summary}，{when}。都安排好了。",
}

NEXT_APPOINTMENT_CAREGIVER = {
    "en": "{patient_name}'s next appointment is {summary}, {when}.",
    "zh-Hans": "{patient_name}的下一个预约是{summary}，{when}。",
}

NEXT_APPOINTMENT_LOCATION = {
    "en": " It is at {location}.",
    "zh-Hans": "地点是{location}。",
}

NO_UPCOMING_APPOINTMENTS = {
    "en": "You have no appointments coming up. I will remind you when one is booked.",
    "zh-Hans": "您接下来没有预约。有安排的时候我会提醒您。",
}

NO_UPCOMING_APPOINTMENTS_CAREGIVER = {
    "en": "{patient_name} has no appointments coming up.",
    "zh-Hans": "{patient_name}接下来没有预约。",
}

AUDIO_TOO_LONG = {
    "en": "That was a bit long, could you say it again more briefly?",
    "zh-Hans": "这段有点长，可以再说一次、简短一点吗？",
}

VOICE_UNCLEAR = {
    "en": "I didn't quite catch that. Could you say it again?",
    "zh-Hans": "我没听清楚，可以再说一次吗？",
}

NO_IMPORTANT_EMAILS = {
    "en": "You have no important emails right now.",
    "zh-Hans": "您现在没有重要的邮件。",
}

# --- M8: Vision & documents ---

VISION_DEGRADED = {
    "en": "I can see you sent a picture but I can't look at it right now.",
    "zh-Hans": "我看到您发了一张图片，但我现在没办法看清楚。",
}

IMAGE_UNREADABLE = {
    "en": "I couldn't open that picture. Could you send it again?",
    "zh-Hans": "我打不开这张图片，可以再发一次吗？",
}

PILL_BOTTLE_SAVED_NAMED = {
    "en": "I can see this is {name}. I've saved it for your caregiver to check.",
    "zh-Hans": "我看到这是{name}。我已经保存下来，让您的照顾者确认一下。",
}

PILL_BOTTLE_SAVED_GENERIC = {
    "en": "I can see a medicine label. I've saved it for your caregiver to check.",
    "zh-Hans": "我看到了一个药品标签。我已经保存下来，让您的照顾者确认一下。",
}

PRESCRIPTION_SAVED = {
    "en": "I can see this is a prescription or medical document. I've saved it for your "
    "caregiver to check.",
    "zh-Hans": "我看到这是一份处方或医疗文件。我已经保存下来，让您的照顾者确认一下。",
}

DOCUMENT_TOO_LONG = {
    "en": "That document is too long for me to read. Could you send a shorter one?",
    "zh-Hans": "这份文件太长了，我读不了。可以发一份短一点的吗？",
}

DOCUMENT_UNREADABLE = {
    "en": "I couldn't open that document. Could you send it again?",
    "zh-Hans": "我打不开这份文件，可以再发一次吗？",
}

DOCUMENT_DEGRADED_EMPTY = {
    "en": "I can see you sent a document but I can't read it right now.",
    "zh-Hans": "我看到您发了一份文件，但我现在没办法读取。",
}

DOCUMENT_OFFER_VOICE = {
    "en": " Would you like me to read this to you?",
    "zh-Hans": " 需要我读给您听吗？",
}

# document.py, patient-uploaded PDF classified as prescription/lab_report/
# discharge_note (or unclassified in degraded mode): the patient is never
# shown the clinical summary at all - it goes straight to the caregiver
# instead (_notify_caregiver_of_document), so the patient does not have to
# be the one to relay it. This is the patient's reply in that case; short
# and safe, no clinical content, no action required of them.
DOCUMENT_SAVED_FOR_CAREGIVER = {
    "en": "I've saved this and let your caregiver know, so they can check it.",
    "zh-Hans": "我已经保存好了，也通知了您的照顾者去确认。",
}

# The caregiver notification itself - carries the actual content (not just
# a "go review" ping), so the caregiver can act on one message rather than
# a round trip through "check candidates" first. {doc_kind_label} is one of
# _DOC_KIND_LABEL_EN/_DOC_KIND_LABEL_ZH in document.py.
CAREGIVER_DOCUMENT_NOTIFY = {
    "en": "{patient_name} sent {doc_kind_label}. Here is what it says: {summary}",
    "zh-Hans": "{patient_name}发送了{doc_kind_label}。内容如下：{summary}",
}

# --- M9: Location, SOS & contacts ---

SOS_ALERT_TO_CONTACT = {
    "en": "URGENT: {patient_name} may need help - their WhatsApp assistant detected an "
    "emergency message.{location} Please check on them now.",
    "zh-Hans": "紧急：{patient_name}可能需要帮助——微信助手检测到一条紧急消息。"
    "{location}请立即联系或前去查看。",
}

SOS_CONFIRMATION = {
    "en": "I have told {name}. Stay where you are. Help is coming.",
    "zh-Hans": "我已经通知了{name}。请留在原地，帮助马上就到。",
}

SOS_NO_CONTACT = {
    "en": "I don't have anyone to call. Please call 995.",
    "zh-Hans": "我这里没有可以联系的人，请拨打995。",
}

SOS_SEND_FAILED = {
    "en": "I'm having trouble reaching your contacts. Please call 995 if you can.",
    "zh-Hans": "我暂时联系不上您的家人。如果可以，请拨打995。",
}

LOCATION_ACK_HOME = {
    "en": "Thank you, I have your location.",
    "zh-Hans": "谢谢，我已经收到您的位置了。",
}

LOCATION_ACK_OUTSIDE_ZONE = {
    "en": "Thank you. I've let your caregiver know where you are.",
    "zh-Hans": "谢谢。我已经把您的位置告诉您的照顾者了。",
}

SAFE_ZONE_ALERT_TO_CONTACT = {
    "en": "{patient_name} shared their location outside their usual safe areas: {place}.",
    "zh-Hans": "{patient_name}分享了不在常去安全区域内的位置：{place}。",
}

LOCATION_ACK_NO_CONTACT = {
    "en": "Thank you for sharing your location. I don't have a caregiver contact saved yet.",
    "zh-Hans": "谢谢您分享位置。我这里还没有保存照顾者的联系方式。",
}

CONTACT_UNREADABLE = {
    "en": "I couldn't read that contact card. Could you send it again?",
    "zh-Hans": "我读不了这张联系人名片，可以再发一次吗？",
}

# §07 §7.11 two-step contact chain. Question 1 grants the smaller permission
# (who to phone), question 2 the larger one (who may write to her records) -
# see app/pipelines/text.py::_confirm_contact_question for why that order.
CONTACT_SAVED_ASK_EMERGENCY = {
    "en": "I've saved {name} as a contact. Should I call them if there's an emergency?",
    "zh-Hans": "我已经把{name}保存为联系人。如果有紧急情况，需要我联系他们吗？",
}

CONTACT_EMERGENCY_NO = {
    "en": "Okay, I won't call {name} in an emergency.",
    "zh-Hans": "好的，紧急情况下我不会联系{name}。",
}

CONTACT_ASK_CAREGIVER = {
    "en": "Do you want {name} to be your caregiver as well?",
    "zh-Hans": "您也想让{name}做您的照顾者吗？",
}

CONTACT_CAREGIVER_YES = {
    "en": "Good. {name} is your caregiver now.",
    "zh-Hans": "好的。{name}现在是您的照顾者。",
}

# A "no" here must not read as undoing question 1 - it says the first answer stands.
CONTACT_CAREGIVER_NO = {
    "en": "Okay. I'll still call {name} if there's an emergency.",
    "zh-Hans": "好的。如果有紧急情况，我还是会联系{name}。",
}

# §17 role detection (app/pipelines/text.py::handle): sent once, the first
# time a caregiver's own inbound message resolves to a pending link, in place
# of answering that message - deterministic and not LLM-generated so the very
# first thing a caregiver reads can't be an improvised, possibly-overpromising
# introduction (the same reasoning as OAUTH_SUCCESS above). Two separate
# sends, not one message - greeting first, capability list second - each
# through outbound.send_text so CHANNEL-1's throttle naturally paces them.
#
# CAREGIVER_LINK_ACTIVATED_COMMANDS advertises `check`/`delete` and
# `reminder` as their own object, plus the general `action parameter`
# grammar - none of that is implemented yet (only the five fixed phrases in
# caregiver.py's _SET_*_RE/_CHECK_CANDIDATES_RE are real: set appointment,
# set bloodwork, set address, set medication, check candidates). This is a
# deliberate choice to advertise the intended command surface ahead of the
# implementation landing (tracked separately) - a caregiver trying one of
# the not-yet-real examples today falls through to _caregiver_qa (free-text
# chat) with no guard on that path, same known gap as an unmatched command
# always had, just now reachable via an example shown to them directly.
CAREGIVER_LINK_ACTIVATED_GREETING = {
    "en": "Hello! I'm Kopi, {patient_name}'s AI assistant. They've added you as their caregiver.",
    "zh-Hans": "您好！我是Kopi，{patient_name}的AI助手。他们已经把您添加为照顾者。",
}

CAREGIVER_LINK_ACTIVATED_COMMANDS = {
    "en": "You can ask me about {patient_name}'s medicines, appointments, or recent emails.\n\n"
    "You can also `set`, `check`, or `delete`:\n"
    "`appointment` • `reminder` • `medication` • `address` • `bloodwork`\n\n"
    "Format: `action parameter`\n\n"
    "Examples:\n"
    "`check medication`\n"
    "`set reminder Take medicine at 8pm`\n"
    "`delete appointment`",
    "zh-Hans": "您可以向我询问{patient_name}的用药、预约或近期邮件。\n\n"
    "您还可以使用`set`（设置）、`check`（查看）或`delete`（删除）：\n"
    "`appointment`（预约）• `reminder`（提醒）• `medication`（用药）• `address`（地址）• "
    "`bloodwork`（验血）\n\n"
    "格式：`action parameter`（动作 参数）\n\n"
    "例如：\n"
    "`check medication`（查看用药）\n"
    "`set reminder Take medicine at 8pm`（设置提醒：晚上8点吃药）\n"
    "`delete appointment`（删除预约）",
}

CHECKIN_PROMPT = {
    "en": "Hello! Where are you now? Tap to share your location with me.",
    "zh-Hans": "您好！您现在在哪里？点一下分享您的位置给我吧。",
}

CHECKIN_NO_RESPONSE_ALERT = {
    "en": "{patient_name} has not replied to a check-in message for 20 minutes.",
    "zh-Hans": "{patient_name}已经20分钟没有回复问候消息了。",
}

# --- M10: Caregiver commands (§17) ---

# set appointment
CAREGIVER_APPOINTMENT_ASK_DATETIME = {
    "en": "When is {patient_name}'s appointment? Please give the date and time, "
    "e.g. '5 August 2026, 2pm'.",
    "zh-Hans": '{patient_name}的预约是什么时候？请提供日期和时间，例如"2026年8月5日下午2点"。',
}

CAREGIVER_APPOINTMENT_DATETIME_UNCLEAR = {
    "en": "I couldn't understand that date and time. Please try again, e.g. '5 August 2026, 2pm'.",
    "zh-Hans": '我没能理解这个日期和时间，请再试一次，例如"2026年8月5日下午2点"。',
}

CAREGIVER_APPOINTMENT_ASK_LOCATION = {
    "en": "Where is the appointment?",
    "zh-Hans": "预约地点在哪里？",
}

CAREGIVER_APPOINTMENT_ASK_PURPOSE = {
    "en": "What is the appointment for?",
    "zh-Hans": "这次预约是为了什么？",
}

CAREGIVER_APPOINTMENT_CONFIRM = {
    "en": "New appointment for {patient_name}: {purpose}, {when}, at {location}. "
    "Should I save this?",
    "zh-Hans": "为{patient_name}新增预约：{purpose}，{when}，地点在{location}。要保存吗？",
}

CAREGIVER_APPOINTMENT_SAVED = {
    "en": "Saved. {patient_name} has been told about the appointment.",
    "zh-Hans": "已保存。已经通知{patient_name}这次预约了。",
}

CAREGIVER_APPOINTMENT_CANCELLED = {
    "en": "Okay, I didn't save that. Send 'set appointment' to try again.",
    "zh-Hans": '好的，没有保存。发送"set appointment"可以重新开始。',
}

# Sent to the patient when a caregiver-created appointment is saved -
# {when} comes from calendar_service.render_when(), never a raw clock time
# (persona voice rule, docs/16).
PATIENT_NEW_APPOINTMENT = {
    "en": "A new appointment has been added: {purpose}, {when}, at {location}.",
    "zh-Hans": "已经新增一个预约：{purpose}，{when}，地点在{location}。",
}

# set bloodwork
CAREGIVER_BLOODWORK_ASK_INTAKE = {
    "en": "Please send {patient_name}'s bloodwork - you can type it, or send a photo "
    "or PDF. Type 'done' when finished.",
    "zh-Hans": "请发送{patient_name}的验血结果——可以直接打字，也可以发送照片或PDF文件。"
    '完成后请输入"done"。',
}

CAREGIVER_BLOODWORK_TEXT_SAVED = {
    "en": "Saved. Send more, or type 'done' when finished.",
    "zh-Hans": '已保存。可以继续发送，完成后请输入"done"。',
}

CAREGIVER_BLOODWORK_MEDIA_SAVED = {
    "en": "Saved that as bloodwork for {patient_name}. Send more, or type 'done' when finished.",
    "zh-Hans": '已保存为{patient_name}的验血记录。可以继续发送，完成后请输入"done"。',
}

CAREGIVER_BLOODWORK_DONE = {
    "en": "Thank you, I've saved {patient_name}'s bloodwork.",
    "zh-Hans": "谢谢，{patient_name}的验血记录已经保存。",
}

# set address
CAREGIVER_ADDRESS_ASK = {
    "en": "What is {patient_name}'s home address?",
    "zh-Hans": "{patient_name}的家庭地址是什么？",
}

CAREGIVER_ADDRESS_SAVED = {
    "en": "Saved. {patient_name} can now ask me where home is.",
    "zh-Hans": "已保存。{patient_name}现在可以问我家在哪里了。",
}

# set medication
CAREGIVER_MEDICATION_ASK_NAME = {
    "en": "What is the name of the medicine?",
    "zh-Hans": "药物的名称是什么？",
}

CAREGIVER_MEDICATION_ASK_DOSE = {
    "en": "What is the dose? For example, '1 tablet' or '5mg'.",
    "zh-Hans": '剂量是多少？例如"1片"或"5毫克"。',
}

# Free text, parsed by gemini_client.parse_medication_schedule() - not a
# fixed menu. Safe because nothing is written from the parse alone: the
# parsed schedule is echoed back in plain language at the confirm step
# (CAREGIVER_MEDICATION_CONFIRM) and only written on an explicit yes, same
# discipline as name and dose above. See the module comment on
# parse_medication_schedule for the full reasoning.
CAREGIVER_MEDICATION_ASK_SCHEDULE = {
    "en": "When should {patient_name} take it? For example, 'once a day in the morning', "
    "'twice a day, before meals', or 'three times a day with food'.",
    "zh-Hans": "{patient_name}应该什么时候吃？例如"
    '"每天早上一次"、"每天两次，饭前"或"每天三次，随餐服用"。',
}

CAREGIVER_MEDICATION_SCHEDULE_UNCLEAR = {
    "en": "I didn't quite understand that. Could you describe it differently? For example, "
    "'once a day in the morning' or 'twice a day, before meals'.",
    "zh-Hans": "我没太明白。可以换个说法吗？例如" '"每天早上一次"或"每天两次，饭前"。',
}

CAREGIVER_MEDICATION_CONFIRM = {
    "en": "{name}, {dose_text}, {schedule_label} - should I add this for {patient_name}?",
    "zh-Hans": "{name}，{dose_text}，{schedule_label}——要为{patient_name}添加这条用药记录吗？",
}

CAREGIVER_MEDICATION_SAVED = {
    "en": "Saved. {patient_name} will get reminders for {name}.",
    "zh-Hans": "已保存。{patient_name}会收到{name}的用药提醒。",
}

CAREGIVER_MEDICATION_CANCELLED = {
    "en": "Okay, I didn't save that. Send 'set medication' to try again.",
    "zh-Hans": '好的，没有保存。发送"set medication"可以重新开始。',
}

# check candidates (§17 §1.2, closes CLAUDE.md SAFETY-1 clause 3)
CAREGIVER_NO_PENDING_CANDIDATES = {
    "en": "There is nothing waiting for review right now.",
    "zh-Hans": "目前没有需要审核的内容。",
}

# {extracted} is the verbatim text_verbatim from the photo, or a plain
# "no text could be read" note - never a guess at the drug name beyond what
# was actually printed (CLAUDE.md SAFETY-1: OCR extraction only, no inference).
CAREGIVER_CANDIDATE_REVIEW_PROMPT = {
    "en": "From a photo, {days_ago} - here's what was on the label:\n\n{extracted}\n\n"
    "Add this as a medication for {patient_name}? (yes/no)",
    "zh-Hans": "来自{days_ago}的照片——标签上的内容是：\n\n{extracted}\n\n"
    "要把这个添加为{patient_name}的用药记录吗？（是/否）",
}

CAREGIVER_CANDIDATE_REJECTED = {
    "en": "Okay, skipped.",
    "zh-Hans": "好的，已跳过。",
}

CAREGIVER_CANDIDATE_REVIEW_DONE = {
    "en": "No more items to review.",
    "zh-Hans": "没有更多需要审核的内容了。",
}

# Sent to the linked caregiver when image.py creates a new pending candidate -
# previously nothing notified anyone (verified this session: the patient was
# told "saved for your caregiver to check" but the caregiver was never
# actually messaged). Free-form text, so only sends if the caregiver's own
# 24h window with this number is open; if not, it queues like any other
# outbound.send_text (app/channels/outbound.py) and is delivered once they
# next message in.
CAREGIVER_CANDIDATE_NOTIFY = {
    "en": "{patient_name} sent a photo of a medicine label. Send 'check candidates' to review it.",
    "zh-Hans": '{patient_name}发送了一张药品标签的照片。发送"check candidates"可以查看。',
}

# Patient-side reads (§17 §7)
NO_BLOODWORK = {
    "en": "I don't have your bloodwork on file. I can ask your caregiver.",
    "zh-Hans": "我这里没有您的验血记录，我可以帮您问问照顾者。",
}

NO_HOME_ADDRESS = {
    "en": "I don't have your home address on file. I can ask your caregiver.",
    "zh-Hans": "我这里没有您的家庭地址，我可以帮您问问照顾者。",
}

# {summary} is whichever of summary_en/zh/extracted_text the document
# actually has (app/pipelines/text.py::_bloodwork_query) - rendered
# verbatim, never re-summarised by a model (see get_latest_bloodwork's
# docstring for why this stays out of the general free-text QA path).
BLOODWORK_RESULT = {
    "en": "Here is your latest bloodwork: {summary}",
    "zh-Hans": "这是您最新的验血结果：{summary}",
}

BLOODWORK_RESULT_CAREGIVER = {
    "en": "{patient_name}'s latest bloodwork: {summary}",
    "zh-Hans": "{patient_name}最新的验血结果：{summary}",
}

NO_BLOODWORK_CAREGIVER = {
    "en": "There is no bloodwork on file for {patient_name}.",
    "zh-Hans": "目前没有{patient_name}的验血记录。",
}

BLOOD_TYPE_RESULT = {
    "en": "Your blood type is {blood_type}.",
    "zh-Hans": "您的血型是{blood_type}型。",
}

BLOOD_TYPE_RESULT_CAREGIVER = {
    "en": "{patient_name}'s blood type is {blood_type}.",
    "zh-Hans": "{patient_name}的血型是{blood_type}型。",
}

# Persona rule (docs/16): answers about "where am I" / "what's happening"
# end with a small reassurance - the caregiver version doesn't, per the
# caregiver persona's own "no reassurance, no softening" rule.
HOME_ADDRESS_RESULT = {
    "en": "Your home is at {address}. You are safe.",
    "zh-Hans": "您的家在{address}。您很安全。",
}

HOME_ADDRESS_RESULT_CAREGIVER = {
    "en": "{patient_name}'s home address is {address}.",
    "zh-Hans": "{patient_name}的家庭地址是{address}。",
}

NO_HOME_ADDRESS_CAREGIVER = {
    "en": "There is no home address on file for {patient_name}.",
    "zh-Hans": "目前没有{patient_name}的家庭地址记录。",
}
