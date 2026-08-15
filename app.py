import streamlit as st
import streamlit.components.v1 as components
from agent import quiet_mode
from agent.conversation_store import append_message, load_conversation
from agent.executor import execute_task_stream
from agent.tts_control import speak_interruptible, stop_speaking


st.set_page_config(
    page_title="Jarvis",
    page_icon="🤖"
)


st.title("🤖 Jarvis")
st.caption("Your personal AI agent")

# Query params are read early, before anything renders, because the wake
# word component below needs to know the current conversation-mode state
# (active vs. passive) to initialize its JS in the right state — Streamlit
# reruns the whole script on every interaction, so this has to be resolved
# fresh at the top each time, not cached from a previous run.
if "conversation_active" not in st.session_state:
    st.session_state.conversation_active = False

_mode_param = st.query_params.get("mode")
if _mode_param:
    st.session_state.conversation_active = (_mode_param == "active")

_voice_text = st.query_params.get("voice")
_pending_prompt = None
if _voice_text and _voice_text != st.session_state.get("last_voice_text"):
    st.session_state.last_voice_text = _voice_text
    _pending_prompt = _voice_text
    if st.query_params.get("interrupt"):
        stop_speaking()

# Quiet-mode commands are handled before messages render or reach the
# executor. State is shared with the native menu-bar process, rather than
# being trapped in this browser tab's session_state.
if _pending_prompt:
    _quiet_action = quiet_mode.classify(_pending_prompt)
    if _quiet_action in (
        quiet_mode.QuietAction.ENTER_QUIET,
        quiet_mode.QuietAction.ENTER_SLEEP,
        quiet_mode.QuietAction.ENTER_OFF,
    ):
        stop_speaking()
        st.session_state.conversation_active = False
        _pending_prompt = None
    elif _quiet_action == quiet_mode.QuietAction.IGNORE:
        _pending_prompt = None

if st.query_params.get("mode") or st.query_params.get("voice"):
    st.query_params.clear()


with st.sidebar:
    st.markdown("#### ⚙️ Settings")
    speak_replies = st.checkbox("🔊 Speak replies aloud", value=False)
    wake_word_enabled = st.checkbox(
        '🎙️ Wake word ("Jarvis")',
        value=False,
        help='Say "Jarvis" once to start a conversation — after that, everything you say is sent to Jarvis without needing to say "Jarvis" again, until you say something like "Jarvis, that\'s all" to end it. Off by default — the status line below always shows the current state.',
    )

    st.divider()
    st.markdown("##### 🎤 Voice input")
    st.caption("Chrome or Safari only.")

    # Live browser dictation: transcribes continuously while you talk (using
    # Chrome/Safari's built-in speech recognition, not a server round-trip),
    # so by the time you stop talking the text is already there — no
    # separate "record, then transcribe, then think" delay.
    components.html(
        """
        <div style="display:flex;align-items:center;gap:10px;font-family:-apple-system,sans-serif;">
            <button id="micBtn" style="background:#3b3;color:white;border:none;
                border-radius:50%;width:40px;height:40px;font-size:16px;cursor:pointer;">
                🎤
            </button>
            <span id="micStatus" style="color:#9098A8;font-size:13px;">Click to speak</span>
        </div>
        <script>
        const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
        const micBtn = document.getElementById('micBtn');
        const micStatus = document.getElementById('micStatus');

        if (!SpeechRecognition) {
            micStatus.innerText = 'Not supported in this browser';
            micBtn.disabled = true;
        } else {
            const recognition = new SpeechRecognition();
            recognition.continuous = false;
            recognition.interimResults = true;
            recognition.lang = 'en-US';

            recognition.onstart = () => {
                micBtn.style.background = '#e33';
                micStatus.innerText = 'Listening...';
            };

            recognition.onresult = (event) => {
                let interim = '';
                let final = '';
                for (let i = event.resultIndex; i < event.results.length; i++) {
                    if (event.results[i].isFinal) {
                        final += event.results[i][0].transcript;
                    } else {
                        interim += event.results[i][0].transcript;
                    }
                }
                if (interim) micStatus.innerText = interim;
                if (final) {
                    micStatus.innerText = final;
                    const url = new URL(window.top.location.href);
                    url.searchParams.set('voice', final.trim());
                    url.searchParams.set('interrupt', '1');
                    url.searchParams.set('t', Date.now());
                    window.top.location.href = url.toString();
                }
            };

            recognition.onerror = (event) => {
                micStatus.innerText = 'Error: ' + event.error;
                micBtn.style.background = '#3b3';
            };

            recognition.onend = () => {
                micBtn.style.background = '#3b3';
            };

            micBtn.onclick = () => {
                micStatus.innerText = 'Starting...';
                recognition.start();
            };
        }
        </script>
        """,
        height=50,
    )

    # Wake word + continuous conversation mode. Say "Jarvis" once to enter
    # conversation mode (green status) — every utterance after that is sent
    # straight to Jarvis with no "Jarvis" prefix needed, until an exit
    # phrase ("jarvis that's all" / "jarvis bye" / "jarvis stop" / "jarvis
    # close", in either word order) ends it and returns to passive
    # wake-word-only listening (grey status). Off by default, and always
    # shows which state it's in.
    #
    # Honest limitation: Streamlit reruns the whole page on every
    # interaction, which re-creates this component from scratch each time —
    # so listening briefly restarts after every response instead of running
    # in one unbroken session. The current mode (active/passive) is passed
    # back in from Python (via session_state, set from the 'mode' query
    # param) so it resumes in the right state instead of always resetting
    # to passive.
    if wake_word_enabled:
        _initial_mode = "active" if st.session_state.conversation_active else "passive"

        components.html(
            f"""
            <div id="wakeStatus" style="color:#9098A8;font-size:13px;font-family:-apple-system,sans-serif;">
                👂 Listening for "Jarvis"...
            </div>
            <script>
            const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
            const statusEl = document.getElementById('wakeStatus');

            if (!SpeechRecognition) {{
                statusEl.innerText = 'Wake word not supported in this browser';
            }} else {{
                const recognition = new SpeechRecognition();
                recognition.continuous = true;
                recognition.interimResults = true;
                recognition.lang = 'en-US';

                let mode = '{_initial_mode}';  // 'passive' or 'active', seeded from Python
                let commandBuffer = '';
                let shouldRestart = true;
                let inactivityTimer = null;

                const EXIT_WORDS = /\\b(bye|goodbye|stop|close|done|finished|that'?s all)\\b/i;

                function isExitPhrase(text) {{
                    return /\\bjarvis\\b/i.test(text) && EXIT_WORDS.test(text);
                }}

                function stripWakeWord(text) {{
                    // Remove "jarvis" from wherever it appears, not just a
                    // prefix — "hi Jarvis" and "Jarvis, open Walmart" both
                    // need to work, and the greeting word matters here
                    // ("hi Jarvis" previously lost "hi" entirely, since only
                    // text *after* "jarvis" was kept — nothing was left to
                    // submit, so it silently did nothing).
                    return text.replace(/\\bjarvis\\b/gi, ' ').replace(/\\s+/g, ' ').trim();
                }}

                function updateStatus() {{
                    if (mode === 'active') {{
                        statusEl.innerHTML = '🟢 <b>Conversation mode</b> — listening, say \\'Jarvis, that\\'s all\\' to end';
                    }} else {{
                        statusEl.innerText = '👂 Listening for "Jarvis"...';
                    }}
                }}
                updateStatus();

                function armInactivityTimeout() {{
                    if (inactivityTimer) clearTimeout(inactivityTimer);
                    // Safety net: if conversation mode is left running with
                    // no one talking, drop back to passive after 2 minutes
                    // rather than staying in "everything I say is a
                    // command" mode indefinitely.
                    inactivityTimer = setTimeout(endConversation, 120000);
                }}

                function goToUrl(params) {{
                    const url = new URL(window.top.location.href);
                    for (const [key, value] of Object.entries(params)) {{
                        url.searchParams.set(key, value);
                    }}
                    url.searchParams.set('t', Date.now());
                    window.top.location.href = url.toString();
                }}

                function submitCommand(text) {{
                    goToUrl({{voice: text, interrupt: '1', mode: 'active'}});
                }}

                function endConversation() {{
                    statusEl.innerText = '👋 Ending conversation...';
                    goToUrl({{mode: 'passive', interrupt: '1'}});
                }}

                recognition.onresult = (event) => {{
                    for (let i = event.resultIndex; i < event.results.length; i++) {{
                        const transcript = event.results[i][0].transcript;
                        const isFinal = event.results[i].isFinal;
                        armInactivityTimeout();

                        if (mode === 'passive') {{
                            const lower = transcript.toLowerCase();
                            const idx = lower.indexOf('jarvis');
                            if (idx !== -1) {{
                                if (isExitPhrase(transcript)) {{ return; }}  // e.g. stray "jarvis bye" while already passive -- ignore
                                mode = 'active';
                                updateStatus();
                                const rest = stripWakeWord(transcript);
                                if (rest) commandBuffer = rest;
                                if (isFinal && commandBuffer) {{
                                    submitCommand(commandBuffer);
                                    return;
                                }}
                            }}
                        }} else {{
                            commandBuffer = transcript;
                            statusEl.innerHTML = '🟢 ' + commandBuffer;
                            if (isFinal) {{
                                if (isExitPhrase(commandBuffer)) {{ endConversation(); return; }}
                                submitCommand(commandBuffer.trim());
                                return;
                            }}
                        }}
                    }}
                }};

                recognition.onerror = (event) => {{
                    if (event.error === 'not-allowed' || event.error === 'service-not-allowed') {{
                        statusEl.innerText = '🚫 Microphone permission denied';
                        shouldRestart = false;
                    }} else if (event.error !== 'no-speech' && event.error !== 'aborted') {{
                        statusEl.innerText = 'Error: ' + event.error;
                    }}
                }};

                recognition.onend = () => {{
                    if (shouldRestart) {{
                        setTimeout(() => {{ try {{ recognition.start(); }} catch (e) {{}} }}, 300);
                    }}
                }};

                if (mode === 'active') armInactivityTimeout();
                recognition.start();
            }}
            </script>
            """,
            height=30,
        )


# Conversation is loaded fresh from shared storage on every run (not
# cached once in session_state) so any device that opens this page — your
# Mac, your phone on the same Wi-Fi (see the "Network URL" Streamlit
# printed at startup) — sees the real, current conversation rather than
# its own stale, disconnected copy.
st.session_state.messages = load_conversation()


# Display previous messages
for message in st.session_state.messages:

    with st.chat_message(message["role"]):
        st.write(message["content"])


# Chat box
_typed_prompt = st.chat_input("What do you need help with?")
prompt = _typed_prompt or _pending_prompt
_prompt_source = "chat" if _typed_prompt else "voice"


if prompt:

    # Typed messages need the same pre-model filter as browser voice.
    # Avoid classifying _pending_prompt twice: WAKE has already disabled
    # quiet mode and is intentionally passed through as a greeting.
    if prompt != _pending_prompt:
        _quiet_action = quiet_mode.classify(prompt)
        if _quiet_action in (
            quiet_mode.QuietAction.ENTER_QUIET,
            quiet_mode.QuietAction.ENTER_SLEEP,
            quiet_mode.QuietAction.ENTER_OFF,
        ):
            stop_speaking()
            st.session_state.conversation_active = False
            st.stop()
        if _quiet_action == quiet_mode.QuietAction.IGNORE:
            st.stop()

    # Add user message
    st.session_state.messages = append_message(
        {"role": "user", "content": prompt}
    )


    with st.chat_message("user"):
        st.write(prompt)


    # Run agent, streaming text in as it's generated
    with st.chat_message("assistant"):

        response = st.write_stream(execute_task_stream(
            prompt, st.session_state.messages, source=_prompt_source,
        ))

        if speak_replies:
            speak_interruptible(response)


    # Save assistant response
    st.session_state.messages = append_message(
        {"role": "assistant", "content": response}
    )
