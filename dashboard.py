import streamlit as st
import streamlit.components.v1 as components
import requests
import json
import html as html_escape_lib
import pandas as pd
import numpy as np
import os

API_URL = "http://127.0.0.1:8000"


def render_copyable_json(data, height=420):
    """
    JSON'u, saglam bir kopyalama butonuyla birlikte gosterir.
    Streamlit surumune gore st.code()'un yerlesik kopyalama ikonu
    her zaman gorunmeyebiliyor/calismayabiliyor; bu yuzden kendi
    HTML/JS bilesenimizi kullaniyoruz (navigator.clipboard, olmazsa
    document.execCommand fallback'i ile).
    """
    formatted = json.dumps(data, indent=2, ensure_ascii=False)
    # <pre> icine gomulecek metin icin HTML-escape
    escaped_for_html = html_escape_lib.escape(formatted)
    # JS string literali icin guvenli escape (json.dumps tirnak/backslash/yeni satiri halleder)
    escaped_for_js = json.dumps(formatted)

    component_html = f"""
    <div style="position:relative; font-family: 'Source Code Pro', monospace;">
      <button id="copyJsonBtn"
        style="position:absolute; top:8px; right:8px; z-index:10;
               padding:6px 14px; background:#2563eb; color:#fff; border:none;
               border-radius:6px; cursor:pointer; font-size:13px; font-weight:600;">
        📋 Kopyala
      </button>
      <pre style="background:#0e1117; color:#7ee787; padding:16px; padding-top:44px;
                   border-radius:8px; overflow:auto; max-height:{height}px;
                   margin:0; font-size:13px; line-height:1.5;">{escaped_for_html}</pre>
    </div>
    <script>
      (function() {{
        const jsonText = {escaped_for_js};
        const btn = document.getElementById('copyJsonBtn');
        btn.addEventListener('click', async function() {{
          let ok = true;
          try {{
            await navigator.clipboard.writeText(jsonText);
          }} catch (err) {{
            try {{
              const ta = document.createElement('textarea');
              ta.value = jsonText;
              ta.style.position = 'fixed';
              ta.style.opacity = '0';
              document.body.appendChild(ta);
              ta.focus();
              ta.select();
              document.execCommand('copy');
              document.body.removeChild(ta);
            }} catch (err2) {{
              ok = false;
            }}
          }}
          btn.textContent = ok ? '✅ Kopyalandı' : '⚠️ Kopyalanamadı';
          setTimeout(function() {{ btn.textContent = '📋 Kopyala'; }}, 1800);
        }});
      }})();
    </script>
    """
    components.html(component_html, height=height + 60, scrolling=True)

st.set_page_config(page_title="Enterprise Journey Simulator & AI Platform", layout="wide")
st.title("🚀 Enterprise Customer Journey Orchestrator")

if "sim_results" not in st.session_state:
    st.session_state["sim_results"] = None

if "ab_results" not in st.session_state:
    st.session_state["ab_results"] = None

if "ai_results" not in st.session_state:
    st.session_state["ai_results"] = None

# Örnek Journey JSON Varsayılan Şablonları (Journey A & B)
DEFAULT_JOURNEY_A = {
    "journey_id": "journey_001_A",
    "initial_node": "welcome_push",
    "nodes": {
        "welcome_push": {
            "type": "push",
            "on_consent_blocked": "consent_exit",
            "on_freq_cap_blocked": "freq_exit",
            "transitions": [{"target": "wait_delay", "weight": 1.0}]
        },
        "wait_delay": {
            "type": "wait",
            "duration_hours": 12,
            "transitions": [{"target": "conversion_exit", "weight": 0.35}, {"target": "dropoff_exit", "weight": 0.65}]
        },
        "conversion_exit": {"type": "exit"},
        "dropoff_exit": {"type": "exit"},
        "consent_exit": {"type": "exit"},
        "freq_exit": {"type": "exit"}
    }
}

DEFAULT_JOURNEY_B = {
    "journey_id": "journey_001_B",
    "initial_node": "welcome_push",
    "nodes": {
        "welcome_push": {
            "type": "push",
            "on_consent_blocked": "consent_exit",
            "on_freq_cap_blocked": "freq_exit",
            "transitions": [{"target": "wait_delay", "weight": 1.0}]
        },
        "wait_delay": {
            "type": "wait",
            "duration_hours": 6,
            "transitions": [{"target": "followup_email", "weight": 0.5}, {"target": "dropoff_exit", "weight": 0.5}]
        },
        "followup_email": {
            "type": "email",
            "on_consent_blocked": "consent_exit",
            "on_freq_cap_blocked": "freq_exit",
            "transitions": [{"target": "conversion_exit", "weight": 0.60}, {"target": "dropoff_exit", "weight": 0.40}]
        },
        "conversion_exit": {"type": "exit"},
        "dropoff_exit": {"type": "exit"},
        "consent_exit": {"type": "exit"},
        "freq_exit": {"type": "exit"}
    }
}

# --- SIDEBAR & PERSONA ORANI KONTROLÜ (Nokta 1) ---
with st.sidebar:
    st.header("⚙️ Simülasyon Ayarları")
    cohort_size = st.slider("Synthetic Cohort Büyüklüğü", 1000, 50000, 10000, step=1000)
    monte_carlo_runs = st.slider("Monte Carlo İterasyon Sayısı", 1, 30, 10)
    
    st.subheader("👥 Persona Dağılımı")
    vip_p = st.slider("VIP %", 0, 100, 20)
    new_p = st.slider("New User %", 0, 100, 40)
    inact_p = st.slider("Inactive %", 0, 100, 25)
    churn_p = st.slider("Churn Risk %", 0, 100, 15)

    # 1. Persona Toplam Oranı Doğrulaması
    total_ratio = vip_p + new_p + inact_p + churn_p
    if total_ratio != 100:
        st.error(f"⚠️ Persona toplamı %100 olmalı. Şu an: %{total_ratio}")
        st.stop()
    else:
        st.success("✅ Persona dağılımı: %100")

# --- JOURNEY JSON EDİTÖRLERİ (A/B Test İçin A ve B) ---
st.subheader("📝 Journey JSON Editors (A/B Test Kurguları)")
col_json_a, col_json_b = st.columns(2)

with col_json_a:
    with st.expander("Journey A (Mevcut Kurgu)", expanded=False):
        json_input_a = st.text_area(
            "Journey A JSON:",
            value=json.dumps(DEFAULT_JOURNEY_A, indent=2),
            height=250
        )
        try:
            current_journey = json.loads(json_input_a)
        except json.JSONDecodeError as e:
            st.error(f"Journey A Geçersiz JSON: {e}")
            st.stop()

with col_json_b:
    with st.expander("Journey B (Karşılaştırılacak / Optimize Kurgu)", expanded=False):
        json_input_b = st.text_area(
            "Journey B JSON:",
            value=json.dumps(DEFAULT_JOURNEY_B, indent=2),
            height=250
        )
        try:
            journey_b_data = json.loads(json_input_b)
        except json.JSONDecodeError as e:
            st.error(f"Journey B Geçersiz JSON: {e}")
            st.stop()

# Topolojik Grafik Görselleştirme
if os.path.exists("journey_graph.html"):
    st.subheader("🕸️ Topolojik Akış Grafiği")
    with open("journey_graph.html", "r", encoding="utf-8") as f:
        st.components.v1.html(f.read(), height=250, scrolling=True)

st.markdown("---")

tab_sim, tab_ab, tab_ai = st.tabs(["⚡ Simülasyon & Analiz Paneli", "⚔️ A/B Test Karşılaştırması", "🤖 AI Insight & Diagnosis"])

with tab_sim:
    # --- SIMULATION RUNNER WITH TIMEOUT (Nokta 2) ---
    if st.button("🚀 Simülasyonu Çalıştır (SimPy + Monte Carlo)"):
        payload = {
            "journey": current_journey,
            "cohort_size": cohort_size,
            "monte_carlo_runs": monte_carlo_runs,
            "user_segments": {
                "VIP": vip_p / 100,
                "New User": new_p / 100,
                "Inactive": inact_p / 100,
                "Churn Risk": churn_p / 100
            }
        }
        try:
            with st.spinner("Simülasyon çalıştırılıyor (SimPy & Monte Carlo)..."):
                # 2. Timeout Eklendi (120 saniye)
                res = requests.post(f"{API_URL}/journey/simulate", json=payload, timeout=120)
                
            if res.status_code == 200:
                st.session_state["sim_results"] = res.json()
                st.success("Simülasyon Başarıyla Tamamlandı!")
            else:
                st.error(f"Simülasyon Hatası: {res.text}")
        except requests.exceptions.Timeout:
            st.error("⌛ Simülasyon zaman aşımına uğradı (120 sn). Monte Carlo run sayısını veya cohort boyutunu düşürmeyi deneyin.")
        except Exception as e:
            st.error(f"Bağlantı Hatası: {e}")

    # Simülasyon Sonuçları Ekranı
    if st.session_state["sim_results"]:
        sim_data = st.session_state["sim_results"]
        metrics = sim_data["summary_metrics"]
        channels = metrics["channel_metrics"]
        financials = metrics["financials"]

        st.subheader("📊 Düzeltilmiş KPI & Dönüşüm Metrikleri")
        c1, c2, c3, c4, c5 = st.columns(5)
        c1.metric("Toplam Kullanıcı", f"{metrics['total_users']:,}")
        c2.metric("Converted Users", f"{metrics['converted_users']:,}")
        c3.metric("Conversion Rate", f"%{metrics['conversion_rate']*100:.2f}")
        c4.metric("Consent Blocked", f"{metrics['consent_blocked']:,}")
        c5.metric("Dropoff Users", f"{metrics['dropoff_users']:,}")

        st.markdown("---")

        st.subheader("💰 Kanal Gönderimleri & Maliyet Analizi")
        m1, m2, m3, m4, m5 = st.columns(5)
        m1.metric("Push Sent", f"{channels['push_sent']:,}")
        m2.metric("Email Sent", f"{channels['email_sent']:,}")
        m3.metric("SMS Sent", f"{channels['sms_sent']:,}")
        m4.metric("Total Cost", f"${financials['total_cost_usd']:,}")
        m5.metric("Cost Per Conversion", f"${financials['cost_per_conversion_usd']:.2f}")

        st.markdown("---")

        # --- MONTE CARLO İSTATİSTİKLERİ (Nokta 5) ---
        col_graph, col_stats = st.columns([6, 4])
        mc_df = pd.DataFrame(sim_data["monte_carlo_runs"])
        conv_rates = mc_df["conversion_rate"]

        with col_graph:
            st.subheader("📈 Monte Carlo Dönüşüm Grafiği (%)")
            st.bar_chart(mc_df.set_index("run_id")["conversion_rate"])

        with col_stats:
            st.subheader("📊 Monte Carlo İstatistikleri")
            st.metric("Mean Conversion", f"%{conv_rates.mean():.2f}")
            s1, s2 = st.columns(2)
            s1.metric("Worst Case (Min)", f"%{conv_rates.min():.2f}")
            s2.metric("Best Case (Max)", f"%{conv_rates.max():.2f}")
            st.metric("Std Dev (Standart Sapma)", f"%{conv_rates.std():.2f}")

        st.markdown("---")

        col_persona, _ = st.columns([6, 4])
        with col_persona:
            st.subheader("👥 Persona / Segment Analizi")
            st.dataframe(pd.DataFrame(sim_data["segment_analysis"]), use_container_width=True)

        st.markdown("---")

        # --- EVENT TRACE LIMIT (Nokta 3) ---
        st.subheader("⏱️ SimPy Event Trace Log (Zaman Akışı)")
        trace_df = pd.DataFrame(sim_data["event_traces"])
        if not trace_df.empty:
            # 3. Sadece ilk 100 log gösteriliyor
            st.dataframe(trace_df.head(100), height=250, use_container_width=True)
            st.caption(f"📌 Performans optimizasyonu için toplam {len(trace_df)} olaydan yalnızca ilk 100 tanesi gösterilmektedir.")

# --- SEKMELER:  A/B TEST KARŞILAŞTIRMASI ---
with tab_ab:
    st.subheader(" Journey A / B Karşılaştırmalı Simülasyon Analizi")
    st.write("Aynı cohort ve persona şartlarında iki farklı kurgunun performansını yan yana kıyaslayın.")

    if st.button("⚔️ Kurguları Karşılaştır (Simulate A vs B)"):
        ab_payload = {
            "journey_a": current_journey,
            "journey_b": journey_b_data,
            "cohort_size": cohort_size,
            "monte_carlo_runs": monte_carlo_runs,
            "user_segments": {
                "VIP": vip_p / 100,
                "New User": new_p / 100,
                "Inactive": inact_p / 100,
                "Churn Risk": churn_p / 100
            }
        }
        try:
            with st.spinner("Her iki kurgu aynı koşullarda simüle ediliyor..."):
                res_ab = requests.post(f"{API_URL}/journey/compare", json=ab_payload, timeout=120)

            if res_ab.status_code == 200:
                st.session_state["ab_results"] = res_ab.json()
                st.success("A/B Testi Başarıyla Tamamlandı!")
            else:
                st.error(f"A/B Simülasyon Hatası: {res_ab.text}")
        except requests.exceptions.Timeout:
            st.error("⌛ A/B Simülasyonu zaman aşımına uğradı (120 sn).")
        except Exception as e:
            st.error(f"Bağlantı Hatası: {e}")

    if st.session_state["ab_results"]:
        ab_data = st.session_state["ab_results"]
        winner = ab_data.get("winner", "Journey B")
        diff = ab_data.get("metrics_diff", {})

        st.markdown("---")
        st.success(f"🏆 **Kazanan Kurgu: {winner}**")

        k1, k2, k3 = st.columns(3)
        k1.metric(
            "Dönüşüm Farkı (Impact)", 
            f"%{diff.get('conversion_diff_percent', 0):.2f}",
            delta=f"%{diff.get('conversion_diff_percent', 0):.2f}"
        )
        k2.metric(
            "Maliyet Farkı", 
            f"${diff.get('cost_diff_dollar', 0):.2f}",
            delta=f"${diff.get('cost_diff_dollar', 0):.2f}",
            delta_color="inverse"
        )
        k3.metric("Kazanan Dönüşüm Oranı", f"%{diff.get('winner_conversion_rate', 0):.2f}")

        st.markdown("---")
        st.subheader("📊 Kurguların Performans Kıyaslaması")
        if "comparison_table" in ab_data:
            st.dataframe(pd.DataFrame(ab_data["comparison_table"]), use_container_width=True)

with tab_ai:
    st.subheader("🤖 AI Journey Optimizer & Diagnostician")
    
    if st.session_state["sim_results"] is None:
        st.warning("⚠️ Lütfen önce 'Simülasyon & Analiz Paneli' sekmesinden simülasyonu çalıştırın!")
    else:
        st.info("💡 Simülasyonda hesaplanan gerçek metrikler AI Agent'a aktarılıyor...")

        if st.button("🚀 AI Teşhis Raporunu Üret"):
            real_metrics = st.session_state["sim_results"]["summary_metrics"]

            ai_payload = {
                "journey": current_journey,
                "metrics": real_metrics
            }

            try:
                with st.spinner("AI Agent Journey JSON'ı analiz ediyor ve optimizasyon önerisi üretiyor..."):
                    # AI isteği için de 120 saniye timeout konuldu
                    res = requests.post(f"{API_URL}/journey/optimize", json=ai_payload, timeout=120)

                if res.status_code == 200:
                    st.session_state["ai_results"] = res.json().get("data", {})
                    st.success("AI Analizi Başarıyla Üretildi!")
                else:
                    st.error(f"AI Analiz Hatası: {res.text}")
            except requests.exceptions.Timeout:
                st.error("⌛ AI servisi yanıt verirken zaman aşımına uğradı (120 sn).")
            except Exception as e:
                st.error(f"Bağlantı Hatası: {e}")

        # --- SONUÇ GÖSTERİMİ (buton bloğunun DIŞINDA) ---
        # Bunu bilerek if st.button(...) bloğunun dışına aldık: st.button() tek
        # seferlik bir tetikleyicidir, sonraki her rerun'da (örn. radio/görünüm
        # değiştirince) tekrar False döner. Gösterim butonun içinde kalsaydı,
        # radio'yu değiştirdiğinde sonuç ekrandan kaybolur ve JSON'u tekrar
        # görebilmek için AI'yi yeniden çalıştırman gerekirdi.
        if st.session_state["ai_results"]:
            res_data = st.session_state["ai_results"]

            st.markdown("### 🔍 Teşhis (Diagnosis)")
            st.write(res_data.get("diagnosis"))

            st.markdown("### 💡 Önerilen Eylemler")
            for action in res_data.get("recommended_actions", []):
                st.markdown(f"- {action}")

            st.markdown("### 🛠️ Optimize Edilmiş Journey JSON")

            validation_warnings = res_data.get("validation_warnings", [])
            if validation_warnings:
                warning_text = "\n".join(f"- {w}" for w in validation_warnings)
                st.warning(f"⚠️ AI'nin ürettiği journey'de doğrulanamayan sorunlar var:\n{warning_text}")

            optimized_journey = res_data.get("optimized_journey")
            if optimized_journey:
                view_mode = st.radio(
                    "Görünüm",
                    ["🌳 Ağaç (Tree)", "📋 Kopyalanabilir Kod"],
                    horizontal=True,
                    key="optimized_journey_view_mode"
                )

                if view_mode == "🌳 Ağaç (Tree)":
                    st.json(optimized_journey)
                else:
                    # Streamlit'in st.code() ikonu surume gore calismayabildigi
                    # icin kendi kopyalama butonumuzu (HTML/JS) kullaniyoruz.
                    render_copyable_json(optimized_journey)
                    st.caption("📌 Kod bloğunun sağ üst köşesindeki '📋 Kopyala' butonuyla JSON'u girintili (indent=2) formatta panoya kopyalayabilirsiniz.")
            else:
                st.info("Optimize edilmiş journey bulunamadı.")