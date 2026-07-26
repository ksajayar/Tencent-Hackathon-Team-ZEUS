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
    "en": "Something went wrong connecting your Google account. Please try again by saying 'connect google'.",
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

MEDICATION_ACK_CONFIRMATION = {
    "en": "Good, thank you for letting me know.",
    "zh-Hans": "好的，谢谢您告诉我。",
}

NO_MEDICATIONS = {
    "en": "I don't have any medicines on file for you yet. Please check with your caregiver.",
    "zh-Hans": "我这里还没有您的药物记录，请联系您的照顾者。",
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

DOCUMENT_DEGRADED_PREFIX = {
    "en": "I can't fully summarise this right now, but here is the start of it: ",
    "zh-Hans": "我现在没办法完整总结，但这是文件的开头部分：",
}

DOCUMENT_DEGRADED_EMPTY = {
    "en": "I can see you sent a document but I can't read it right now.",
    "zh-Hans": "我看到您发了一份文件，但我现在没办法读取。",
}

DOCUMENT_OFFER_VOICE = {
    "en": " Would you like me to read this to you?",
    "zh-Hans": " 需要我读给您听吗？",
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

CONTACT_SAVED_ASK_EMERGENCY = {
    "en": "I've saved {name} as a contact. Should I call them if there's an emergency?",
    "zh-Hans": "我已经把{name}保存为联系人。如果有紧急情况，需要我联系他们吗？",
}

CONTACT_EMERGENCY_YES = {
    "en": "Good, I'll call {name} first if you ever need help.",
    "zh-Hans": "好的，如果您需要帮助，我会先联系{name}。",
}

CONTACT_EMERGENCY_NO = {
    "en": "Okay, I won't call {name} in an emergency.",
    "zh-Hans": "好的，紧急情况下我不会联系{name}。",
}

CHECKIN_PROMPT = {
    "en": "Hello! Where are you now? Tap to share your location with me.",
    "zh-Hans": "您好！您现在在哪里？点一下分享您的位置给我吧。",
}

CHECKIN_NO_RESPONSE_ALERT = {
    "en": "{patient_name} has not replied to a check-in message for 20 minutes.",
    "zh-Hans": "{patient_name}已经20分钟没有回复问候消息了。",
}
