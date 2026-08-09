import streamlit as st
from ai.router import choose_ai

st.set_page_config(
    page_title="CampusPilot",
    page_icon="🎓",
)

st.title("🎓 CampusPilot")
st.write("Your AI Student Assistant")

question = st.text_input("What do you need help with?")

if st.button("Send") and question:
    ai = choose_ai(question)

    st.write(f"**CampusPilot chose:** {ai}")

    # API calls will be added after billing is active
    if ai == "claude":
        st.info("Claude would answer this question.")
    else:
        st.info("OpenAI would answer this question.")