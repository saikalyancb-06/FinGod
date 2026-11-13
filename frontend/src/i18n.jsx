import { createContext, useContext } from "react";

// UI strings in English, Hindi and Kannada. AI answers are translated server-side by Sarvam AI.
const S = {
  en: {
    nav_home: "Home", nav_tx: "Transactions", nav_ask: "Ask FinLens", nav_spend: "Smart Spending", nav_plan: "Planner",
    nav_split: "Friends & splits", nav_guard: "Spend Guard", ask_health: "How am I doing this month?", nav_credit: "Credit check", nav_report: "Weekly report", nav_settings: "Settings & privacy",
    good_morning: "Good morning", good_afternoon: "Good afternoon", good_evening: "Good evening",
    safe_to_spend: "Safe to spend", balance: "Balance", inflows: "Inflows", spending: "Spending", for_you: "For you",
    ask_title: "Ask FinLens", ask_sub: "Answers are grounded in backend-calculated numbers. Open “facts used” on any reply to check them.",
    ask_hello: "Hi! Ask me anything about your spending. I answer only from figures FinLens has calculated from your statement. 💬",
    ask_ph: "e.g. Why did my food spending go up?", ask_btn: "Ask",
    ask_suggest: ["Where is most of my money going?", "Why am I running out of money?", "How much did I spend on food delivery?",
      "Can I afford headphones for ₹2,000?", "What subscriptions do I have?", "Am I on track for my goal?"],
    spend_title: "Smart Spending", spend_sub: "Spend better, not just less. Tell me what you want and your budget, and I'll find five real options.",
    spend_hello: "What are you looking for today: food, fashion, or something else to buy? 🛍️",
    spend_ph: "e.g. Chicken biryani under ₹250, delivery", send: "Send", new_search: "↺ New search",
    spend_start: [["🍛", "I want to order biryani"], ["👟", "I need shoes for college"], ["🎧", "I want to buy headphones"], ["🍕", "Pizza for dinner"]],
    listening: "Listening… tap to stop", transcribing: "Transcribing…", speak: "Listen", room: "Your spending room", language: "Language",
  },
  hi: {
    nav_home: "होम", nav_tx: "लेन-देन", nav_ask: "FinLens से पूछें", nav_spend: "स्मार्ट खर्च", nav_plan: "योजना",
    nav_split: "दोस्त और बँटवारा", nav_guard: "खर्च पहरेदार", ask_health: "इस महीने मेरी आर्थिक स्थिति कैसी है?", nav_credit: "क्रेडिट जाँच", nav_report: "साप्ताहिक रिपोर्ट", nav_settings: "सेटिंग्स और गोपनीयता",
    good_morning: "सुप्रभात", good_afternoon: "नमस्कार", good_evening: "शुभ संध्या",
    safe_to_spend: "सुरक्षित खर्च", balance: "बैलेंस", inflows: "आमदनी", spending: "खर्च", for_you: "आपके लिए",
    ask_title: "FinLens से पूछें", ask_sub: "जवाब आपके स्टेटमेंट से गणना किए गए आँकड़ों पर आधारित हैं। जाँचने के लिए “facts used” खोलें।",
    ask_hello: "नमस्ते! अपने खर्च के बारे में कुछ भी पूछें। मैं सिर्फ़ आपके स्टेटमेंट से निकाले गए आँकड़ों से जवाब देता हूँ। 💬",
    ask_ph: "जैसे: मेरा खाने का खर्च क्यों बढ़ा?", ask_btn: "पूछें",
    ask_suggest: ["मेरा ज़्यादातर पैसा कहाँ जा रहा है?", "मेरे पैसे जल्दी क्यों ख़त्म हो जाते हैं?", "मैंने फ़ूड डिलीवरी पर कितना खर्च किया?",
      "क्या मैं ₹2,000 के हेडफ़ोन ले सकता हूँ?", "मेरे कौन-कौन से सब्सक्रिप्शन हैं?", "क्या मैं अपने लक्ष्य की राह पर हूँ?"],
    spend_title: "स्मार्ट खर्च", spend_sub: "कम नहीं, समझदारी से खर्च करें। बताइए क्या चाहिए और बजट क्या है, मैं पाँच असली विकल्प ढूँढूँगा।",
    spend_hello: "आज आप क्या ढूँढ रहे हैं: खाना, कपड़े या कुछ और? 🛍️",
    spend_ph: "जैसे: ₹250 तक चिकन बिरयानी, डिलीवरी", send: "भेजें", new_search: "↺ नई खोज",
    spend_start: [["🍛", "मुझे बिरयानी ऑर्डर करनी है"], ["👟", "कॉलेज के लिए जूते चाहिए"], ["🎧", "हेडफ़ोन खरीदने हैं"], ["🍕", "रात के खाने में पिज़्ज़ा"]],
    listening: "सुन रहा हूँ… रोकने के लिए टैप करें", transcribing: "लिख रहा हूँ…", speak: "सुनें", room: "आपकी खर्च सीमा", language: "भाषा",
  },
  kn: {
    nav_home: "ಮುಖಪುಟ", nav_tx: "ವಹಿವಾಟುಗಳು", nav_ask: "FinLens ಅನ್ನು ಕೇಳಿ", nav_spend: "ಸ್ಮಾರ್ಟ್ ಖರ್ಚು", nav_plan: "ಯೋಜನೆ",
    nav_split: "ಸ್ನೇಹಿತರು ಮತ್ತು ಹಂಚಿಕೆ", nav_guard: "ಖರ್ಚು ಕಾವಲು", ask_health: "ಈ ತಿಂಗಳು ನನ್ನ ಹಣಕಾಸು ಹೇಗಿದೆ?", nav_credit: "ಕ್ರೆಡಿಟ್ ಪರಿಶೀಲನೆ", nav_report: "ವಾರದ ವರದಿ", nav_settings: "ಸೆಟ್ಟಿಂಗ್‌ಗಳು ಮತ್ತು ಗೌಪ್ಯತೆ",
    good_morning: "ಶುಭೋದಯ", good_afternoon: "ನಮಸ್ಕಾರ", good_evening: "ಶುಭ ಸಂಜೆ",
    safe_to_spend: "ಸುರಕ್ಷಿತ ಖರ್ಚು", balance: "ಬ್ಯಾಲೆನ್ಸ್", inflows: "ಆದಾಯ", spending: "ಖರ್ಚು", for_you: "ನಿಮಗಾಗಿ",
    ask_title: "FinLens ಅನ್ನು ಕೇಳಿ", ask_sub: "ಉತ್ತರಗಳು ನಿಮ್ಮ ಸ್ಟೇಟ್‌ಮೆಂಟ್‌ನಿಂದ ಲೆಕ್ಕಹಾಕಿದ ಅಂಕಿಅಂಶಗಳನ್ನು ಆಧರಿಸಿವೆ. ಪರಿಶೀಲಿಸಲು “facts used” ತೆರೆಯಿರಿ.",
    ask_hello: "ನಮಸ್ಕಾರ! ನಿಮ್ಮ ಖರ್ಚಿನ ಬಗ್ಗೆ ಏನು ಬೇಕಾದರೂ ಕೇಳಿ. ನಿಮ್ಮ ಸ್ಟೇಟ್‌ಮೆಂಟ್‌ನಿಂದ ಲೆಕ್ಕಹಾಕಿದ ಅಂಕಿಅಂಶಗಳಿಂದ ಮಾತ್ರ ಉತ್ತರಿಸುತ್ತೇನೆ. 💬",
    ask_ph: "ಉದಾ: ನನ್ನ ಊಟದ ಖರ್ಚು ಯಾಕೆ ಹೆಚ್ಚಾಯಿತು?", ask_btn: "ಕೇಳಿ",
    ask_suggest: ["ನನ್ನ ಹೆಚ್ಚಿನ ಹಣ ಎಲ್ಲಿಗೆ ಹೋಗುತ್ತಿದೆ?", "ನನ್ನ ಹಣ ಬೇಗ ಯಾಕೆ ಖಾಲಿಯಾಗುತ್ತದೆ?", "ಫುಡ್ ಡೆಲಿವರಿಗೆ ನಾನು ಎಷ್ಟು ಖರ್ಚು ಮಾಡಿದೆ?",
      "₹2,000 ಹೆಡ್‌ಫೋನ್ ಕೊಳ್ಳಬಹುದೇ?", "ನನ್ನ ಸಬ್‌ಸ್ಕ್ರಿಪ್ಶನ್‌ಗಳು ಯಾವುವು?", "ನನ್ನ ಗುರಿಯ ದಾರಿಯಲ್ಲಿ ಇದ್ದೇನೆಯೇ?"],
    spend_title: "ಸ್ಮಾರ್ಟ್ ಖರ್ಚು", spend_sub: "ಕಡಿಮೆ ಅಲ್ಲ, ಜಾಣತನದಿಂದ ಖರ್ಚು ಮಾಡಿ. ನಿಮಗೆ ಏನು ಬೇಕು ಮತ್ತು ಬಜೆಟ್ ಹೇಳಿ, ಐದು ನಿಜವಾದ ಆಯ್ಕೆಗಳನ್ನು ಹುಡುಕುತ್ತೇನೆ.",
    spend_hello: "ಇಂದು ನೀವು ಏನು ಹುಡುಕುತ್ತಿದ್ದೀರಿ: ಊಟ, ಬಟ್ಟೆ ಅಥವಾ ಬೇರೇನಾದರೂ? 🛍️",
    spend_ph: "ಉದಾ: ₹250 ಒಳಗೆ ಚಿಕನ್ ಬಿರಿಯಾನಿ, ಡೆಲಿವರಿ", send: "ಕಳುಹಿಸಿ", new_search: "↺ ಹೊಸ ಹುಡುಕಾಟ",
    spend_start: [["🍛", "ನನಗೆ ಬಿರಿಯಾನಿ ಆರ್ಡರ್ ಮಾಡಬೇಕು"], ["👟", "ಕಾಲೇಜಿಗೆ ಶೂ ಬೇಕು"], ["🎧", "ಹೆಡ್‌ಫೋನ್ ಕೊಳ್ಳಬೇಕು"], ["🍕", "ರಾತ್ರಿ ಊಟಕ್ಕೆ ಪಿಜ್ಜಾ"]],
    listening: "ಕೇಳುತ್ತಿದ್ದೇನೆ… ನಿಲ್ಲಿಸಲು ಟ್ಯಾಪ್ ಮಾಡಿ", transcribing: "ಬರೆಯುತ್ತಿದ್ದೇನೆ…", speak: "ಕೇಳಿ", room: "ನಿಮ್ಮ ಖರ್ಚಿನ ಮಿತಿ", language: "ಭಾಷೆ",
  },
};

export const LANGS = [["en", "EN"], ["hi", "हि"], ["kn", "ಕ"]];
export const LangCtx = createContext("en");
export const useLang = () => useContext(LangCtx);
export function useT() {
  const lang = useLang();
  return (k) => (S[lang] && S[lang][k] !== undefined ? S[lang][k] : S.en[k] ?? k);
}
