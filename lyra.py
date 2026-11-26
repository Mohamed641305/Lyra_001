#!/usr/bin/env python3
"""Lyra: a voice assistant for your PC, with a holographic HUD window.
Wake: double clap or say "ليرا" / "Lyra". Then talk; follow-ups need no wake word.
Run: python lyra.py     Autostart on Windows: python lyra.py --autostart
"""
import json, os, re, sys, time, shutil, threading, subprocess, webbrowser, platform, datetime
import urllib.parse, urllib.request
import numpy as np
import speech_recognition as sr
import pyautogui, pyperclip, pyttsx3

try:
    import webview
except ImportError:
    webview = None

OS = platform.system()
HERE = os.path.dirname(os.path.abspath(__file__))
DIR = os.path.join(os.path.expanduser("~"), ".lyra")
CFG, NOTES = os.path.join(DIR, "config.json"), os.path.join(DIR, "notes.json")
DEFAULT = {
    "wake_words": ["lyra", "ليرا", "لايرا", "ليرة", "lira", "لورا"],
    "wake": "both",         # clap | word | both. "word" sends ambient speech to Google's recognizer.
    "lang": "ar-EG",        # ar-EG or en-US
    "clap_level": 0.30,     # lower = more sensitive
    "ollama_model": "llama3.2",
    "gemini_key": "",       # optional, set once
    "voice_reply": True,
    "hand_preview": False,
    "allow_power": False,     # shutdown / restart by voice (still asks for confirmation)
    "allow_any_app": False,   # False = only the apps and sites listed below / in allowed_apps
    "allowed_apps": [],       # e.g. ["word", "excel", "firefox"]
}
pyautogui.FAILSAFE, pyautogui.PAUSE = False, 0


def load_cfg():
    os.makedirs(DIR, exist_ok=True)
    cfg = dict(DEFAULT)
    try:
        cfg.update(json.load(open(CFG, encoding="utf-8")))
    except Exception:
        pass
    json.dump(cfg, open(CFG, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    try:
        os.chmod(CFG, 0o600)
    except Exception:
        pass
    return cfg


C, R, WIN = load_cfg(), sr.Recognizer(), None
SRC, HAND = None, {"on": False}
MOD = "command" if OS == "Darwin" else "ctrl"


def norm(t):
    return re.sub(r"[\u064B-\u0652]", "", t.lower()).translate(str.maketrans("أإآىة", "اااي" + "ه")).strip()


# ------------------------------------------------------------------ HUD + voice out
def ui(state=None, who=None, text=None):
    if not WIN:
        return
    try:
        if state:
            WIN.evaluate_js(f"lyra.set({json.dumps(state)})")
        if text:
            WIN.evaluate_js(f"lyra.log({json.dumps(who)},{json.dumps(text)})")
    except Exception:
        pass


def say(t):
    print(f"[Lyra] {t}", flush=True)
    ui("speaking", "Lyra", t)
    if C["voice_reply"]:
        try:
            e = pyttsx3.init()
            if re.search(r"[\u0600-\u06FF]", t):
                for v in e.getProperty("voices"):
                    if "arab" in (v.name + str(v.languages) + v.id).lower() or "ar-" in v.id.lower():
                        e.setProperty("voice", v.id)
                        break
            e.say(t)
            e.runAndWait()
        except Exception:
            pass
    ui("idle")


# ------------------------------------------------------------------ listening
def is_double_clap(audio):
    x = np.frombuffer(audio.get_raw_data(convert_rate=16000, convert_width=2), dtype=np.int16).astype(np.float32) / 32768
    if len(x) > 40000:
        return False
    idx = np.where(np.abs(x) > C["clap_level"])[0]
    if not len(idx):
        return False
    starts, last = [idx[0]], idx[0]
    for i in idx[1:]:
        if i - last > 1600:
            starts.append(i)
        last = i
    return 2 <= len(starts) <= 3 and 1920 < starts[1] - starts[0] < 14400


def recognize(audio):
    for lang in dict.fromkeys([C["lang"], "en-US"]):
        try:
            return R.recognize_google(audio, language=lang)
        except sr.UnknownValueError:
            continue
        except sr.RequestError:
            print("(speech recognition needs internet)")
            return None
    return None


def listen_once(limit):
    ui("listening")
    try:
        a = R.listen(SRC, timeout=limit, phrase_time_limit=10)
    except sr.WaitTimeoutError:
        ui("idle")
        return None
    ui("thinking")
    return recognize(a)


def confirm():
    say("متأكدة؟")
    a = listen_once(5)
    words = norm(a).split() if a else []
    return 0 < len(words) <= 3 and any(w in {"نعم", "ايوه", "اه", "اكيد", "تمام", "yes", "sure", "ok", "okay"} for w in words)


# ------------------------------------------------------------------ actions
SITES = {"يوتيوب": "youtube.com", "youtube": "youtube.com", "جوجل": "google.com", "google": "google.com",
         "فيسبوك": "facebook.com", "جيميل": "mail.google.com", "gmail": "mail.google.com",
         "واتساب": "web.whatsapp.com", "whatsapp": "web.whatsapp.com", "chatgpt": "chatgpt.com",
         "claude": "claude.ai", "جيتهب": "github.com"}
WIN_APPS = {"كروم": "chrome", "chrome": "chrome", "نوت باد": "notepad", "notepad": "notepad", "الحاسبه": "calc",
            "calculator": "calc", "الملفات": "explorer", "اكسبلورر": "explorer", "سبوتيفاي": "spotify",
            "spotify": "spotify", "vscode": "code", "الاعدادات": "ms-settings:", "settings": "ms-settings:",
            "الرسام": "mspaint", "الكاميرا": "microsoft.windows.camera:"}


def open_app(name):
    name = re.sub(r"[^\w\s.\-]", "", name).strip()
    k = norm(name)
    allowed = set(SITES) | set(WIN_APPS) | {norm(a) for a in C["allowed_apps"]}
    if not C["allow_any_app"] and k not in allowed:
        return f"{name} مش في قائمة البرامج المسموحة. ضيفه في allowed_apps في الإعدادات"
    if k in SITES or ("." in k and " " not in k):
        webbrowser.open("https://" + SITES.get(k, k))
        return f"فتحت {name}"
    try:
        if OS == "Windows":
            subprocess.Popen(["cmd", "/c", "start", "", WIN_APPS.get(k, name)])
        elif OS == "Darwin":
            subprocess.Popen(["open", "-a", name])
        else:
            exe = shutil.which(k) or shutil.which(name)
            subprocess.Popen([exe] if exe else ["xdg-open", name])
        return f"فتحت {name}"
    except Exception:
        return f"معرفتش أفتح {name}"


def screenshot():
    d = os.path.join(os.path.expanduser("~"), "Pictures")
    os.makedirs(d, exist_ok=True)
    pyautogui.screenshot(os.path.join(d, datetime.datetime.now().strftime("lyra_%Y%m%d_%H%M%S.png")))
    return "صورت الشاشة وحفظتها في مجلد Pictures"


def power(kind):
    if not C["allow_power"]:
        return "أمر الإيقاف متعطل للأمان. فعّله من allow_power في الإعدادات"
    if not confirm():
        return "تمام، ألغيت"
    cmd = {"Windows": {"off": ["shutdown", "/s", "/t", "5"], "restart": ["shutdown", "/r", "/t", "5"]},
           "Darwin": {"off": ["osascript", "-e", 'tell app "System Events" to shut down'],
                      "restart": ["osascript", "-e", 'tell app "System Events" to restart']},
           "Linux": {"off": ["systemctl", "poweroff"], "restart": ["systemctl", "reboot"]}}[OS][kind]
    subprocess.Popen(cmd)
    return "حاضر"


def lock():
    subprocess.Popen({"Windows": ["rundll32.exe", "user32.dll,LockWorkStation"],
                      "Darwin": ["pmset", "displaysleepnow"], "Linux": ["loginctl", "lock-session"]}[OS])
    return "قفلت الجهاز"


def press(key, n=1):
    for _ in range(n):
        pyautogui.press(key)
    return "تمام"


def notes(add=None):
    try:
        data = json.load(open(NOTES, encoding="utf-8"))
    except Exception:
        data = []
    if add:
        data.append({"t": datetime.datetime.now().isoformat(timespec="minutes"), "n": add})
        json.dump(data, open(NOTES, "w", encoding="utf-8"), ensure_ascii=False)
        return "هفتكر"
    return "؛ ".join(d["n"] for d in data[-3:]) or "مفيش ملاحظات محفوظة"


def timer(n, unit):
    sec = int(n) * (3600 if unit.startswith(("ساع", "hour")) else 60 if unit.startswith(("دقي", "دقا", "min")) else 1)
    threading.Timer(sec, lambda: say("المؤقت خلص")).start()
    return f"ظبطت مؤقت {n} {unit}"


# ------------------------------------------------------------------ hand mouse
def hand_mouse():
    import cv2, mediapipe as mp
    hands = mp.solutions.hands.Hands(max_num_hands=1, min_detection_confidence=.7, min_tracking_confidence=.6)
    cap, (sw, sh), px, py, last = cv2.VideoCapture(0), pyautogui.size(), None, None, 0
    while HAND["on"]:
        ok, f = cap.read()
        if not ok:
            break
        f = cv2.flip(f, 1)
        res = hands.process(cv2.cvtColor(f, cv2.COLOR_BGR2RGB))
        if res.multi_hand_landmarks:
            h = res.multi_hand_landmarks[0].landmark
            x, y = np.interp(h[5].x, (.25, .75), (0, sw)), np.interp(h[5].y, (.2, .65), (0, sh))
            px, py = (x, y) if px is None else (px + (x - px) / 4, py + (y - py) / 4)
            pyautogui.moveTo(px, py, _pause=False)
            size = np.hypot(h[0].x - h[9].x, h[0].y - h[9].y) or 1
            d = lambda a, b: np.hypot(h[a].x - h[b].x, h[a].y - h[b].y) / size
            now = time.time()
            if now - last > .6 and d(4, 8) < .35:
                pyautogui.click(); last = now
            elif now - last > .6 and d(4, 12) < .35:
                pyautogui.rightClick(); last = now
            for p in h:
                cv2.circle(f, (int(p.x * f.shape[1]), int(p.y * f.shape[0])), 3, (255, 240, 0), -1)
        if C["hand_preview"]:
            cv2.imshow("Lyra", f)
            if cv2.waitKey(1) == 27:
                HAND["on"] = False
    cap.release()
    cv2.destroyAllWindows()


def hand(on):
    if on and not HAND["on"]:
        HAND["on"] = True
        threading.Thread(target=hand_mouse, daemon=True).start()
        return "شغلت التحكم باليد. اقرص بالسبابة للنقر وبالوسطى للنقر اليمين"
    HAND["on"] = False
    return "وقفت التحكم باليد"


# ------------------------------------------------------------------ AI fallback (free)
def post(url, body):
    req = urllib.request.Request(url, json.dumps(body).encode(), {"Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(req, timeout=60))


def ask_ai(q):
    sysmsg = "You are Lyra, a concise voice assistant. Reply in the user's language, at most two short sentences."
    try:
        return post("http://localhost:11434/api/generate",
                    {"model": C["ollama_model"], "system": sysmsg, "prompt": q, "stream": False})["response"].strip()
    except Exception:
        pass
    if C["gemini_key"]:
        try:
            u = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key=" + C["gemini_key"]
            return post(u, {"systemInstruction": {"parts": [{"text": sysmsg}]},
                            "contents": [{"role": "user", "parts": [{"text": q}]}]})["candidates"][0]["content"]["parts"][0]["text"].strip()
        except Exception:
            return "تعذر الاتصال بـ Gemini"
    return f"مش فاهمة الأمر. للردود الذكية المجانية ثبّت Ollama، أو حط مفتاح Gemini مرة واحدة في {CFG}"


# ------------------------------------------------------------------ router
def run(raw):
    n = norm(raw)
    ui(None, "أنت", raw)
    print(f"> {raw}")
    try:
        open(os.path.join(DIR, "log.txt"), "a", encoding="utf-8").write(f"{datetime.datetime.now():%Y-%m-%d %H:%M:%S}  {raw}\n")
    except Exception:
        pass
    S = lambda s: say(s) or True
    if re.search(r"(وقف|اطفي|اقفل|stop).*(اليد|hand)", n): return S(hand(False))
    if re.search(r"(شغل|فعل|ابدا|start|enable).*(اليد|hand|الماوس|mouse)", n): return S(hand(True))
    if re.search(r"(اقفل|اغلق|سكر|وقف).*(البرنامج|ليرا)|goodbye|\bexit\b|\bquit\b|وداعا", n):
        say("إلى اللقاء"); HAND["on"] = False; os._exit(0)
    if re.search(r"(اقفل|قفل|lock).*(الجهاز|الشاشه|الكمبيوتر|screen|computer)", n): return S(lock())
    if re.search(r"(اطفي|اغلق|shutdown|turn off).*(الجهاز|الكمبيوتر|computer)", n): return S(power("off"))
    if re.search(r"(اعد تشغيل|ريستارت|restart)", n): return S(power("restart"))
    m = re.search(r"(?:تذكر|فكرني|remember)\s+(.+)", raw, re.I)
    if m: return S(notes(m.group(1)))
    if re.search(r"(ملاحظاتي|كنت قلتلك|my notes|what did i)", n): return S(notes())
    m = re.search(r"(\d+)\s*(ساعه|ساعات|دقيقه|دقايق|دقائق|ثانيه|ثواني|hours?|minutes?|seconds?)", n)
    if m and re.search(r"مؤقت|منبه|timer|remind|فكرني", n): return S(timer(m.group(1), m.group(2)))
    if re.search(r"(وقف|اوقف|pause|play|كمل|resume).*(الاغنيه|الموسيقي|music|song|الفيديو)", n): return S(press("playpause"))
    if re.search(r"(التاليه|الاغنيه الجايه|next)", n): return S(press("nexttrack"))
    if re.search(r"(السابقه|previous)", n): return S(press("prevtrack"))
    m = re.search(r"(?:شغل|play|ابحث عن|search)\s+(.+?)\s+(?:علي|في|on)\s*(?:يوتيوب|youtube)", n)
    if m:
        webbrowser.open("https://www.youtube.com/results?search_query=" + urllib.parse.quote(m.group(1)))
        return S("بدور على " + m.group(1) + " في يوتيوب")
    m = re.search(r"^(?:افتح|شغل|open|launch)\s+(.+)", n)
    if m: return S(open_app(m.group(1)))
    m = re.search(r"(?:ابحث عن|دور علي|search for|google)\s+(.+)", n)
    if m:
        webbrowser.open("https://www.google.com/search?q=" + urllib.parse.quote(m.group(1)))
        return S("بدور على " + m.group(1))
    m = re.search(r"^(?:اكتب|type|write)\s+(.+)", raw, re.I)
    if m:
        pyperclip.copy(m.group(1).replace("\n", " ")); pyautogui.hotkey(MOD, "v")
        return S("كتبت")
    if re.search(r"(ارفع|زود|علي|up).*(الصوت|volume)", n): return S(press("volumeup", 5))
    if re.search(r"(اخفض|وطي|قلل|down|lower).*(الصوت|volume)", n): return S(press("volumedown", 5))
    if re.search(r"(اكتم|mute)", n): return S(press("volumemute"))
    if re.search(r"(صور الشاشه|لقطه|screenshot)", n): return S(screenshot())
    if re.search(r"(انزل|scroll down)", n): pyautogui.scroll(-700); return True
    if re.search(r"(اطلع|scroll up)", n): pyautogui.scroll(700); return True
    if re.search(r"(صغر|minimize|سطح المكتب)", n):
        pyautogui.hotkey("command" if OS == "Darwin" else "win", "d"); return S("تمام")
    if re.search(r"(اقفل|سكر|close).*(النافذه|window)", n):
        if not confirm(): return S("تمام، ألغيت")
        pyautogui.hotkey(*(("command", "w") if OS == "Darwin" else ("alt", "f4"))); return True
    if re.search(r"(الساعه كام|الوقت|what time)", n): return S(datetime.datetime.now().strftime("الساعة %I:%M %p"))
    if re.search(r"(التاريخ|النهارده|what date)", n): return S(datetime.datetime.now().strftime("%Y-%m-%d"))
    return S(ask_ai(raw))


def session(cmd):
    if not cmd.strip():
        say("أيوه؟")
        cmd = listen_once(8)
    while cmd:
        run(cmd)
        cmd = listen_once(6)
    ui("idle")


def assistant():
    global SRC
    time.sleep(1.5)
    with sr.Microphone() as SRC:
        R.dynamic_energy_threshold, R.pause_threshold = True, .5
        print("Calibrating microphone...")
        R.adjust_for_ambient_noise(SRC, 1)
        print("Config:", CFG)
        say("ليرا جاهزة. صفقي مرتين أو نادي اسمي")
        wakes = [norm(w) for w in C["wake_words"]]
        while True:
            try:
                audio = R.listen(SRC, timeout=1, phrase_time_limit=3)
            except sr.WaitTimeoutError:
                continue
            if C["wake"] in ("clap", "both") and is_double_clap(audio):
                session("")
                continue
            if C["wake"] in ("word", "both"):
                t = recognize(audio)
                n = norm(t) if t else ""
                for w in wakes:
                    if w in n:
                        session(n.split(w, 1)[1])
                        break


def main():
    global WIN
    if "--autostart" in sys.argv and OS == "Windows":
        st = os.path.join(os.environ["APPDATA"], r"Microsoft\Windows\Start Menu\Programs\Startup", "lyra.bat")
        open(st, "w").write(f'@echo off\nstart "" "{sys.executable.replace("python.exe", "pythonw.exe")}" "{os.path.abspath(__file__)}"\n')
        print("Lyra will start with Windows:", st)
        return
    if webview:
        WIN = webview.create_window("Lyra", os.path.join(HERE, "ui.html"), width=980, height=680, background_color="#020a10")
        webview.start(assistant)
    else:
        print("pywebview not installed: running without the HUD window.")
        assistant()


if __name__ == "__main__":
    main()
