from __future__ import annotations
import asyncio, csv, json, re, time
from datetime import datetime, timezone
from pathlib import Path
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError

ROOT = Path(__file__).resolve().parent
OUTPUT = ROOT / 'output'

def stamp(): return datetime.now(timezone.utc).isoformat(timespec='seconds')
def emit(callback, status, message, **extra):
    callback({"time": stamp(), "status": status, "message": message, **extra})
def text_norm(s): return re.sub(r'\s+', ' ', (s or '')).strip().casefold()

async def run_qa(cfg, answer_data, username, password, emit_event):
    """Run authorized QA using predefined test fixtures, never AI-generated answers."""
    OUTPUT.mkdir(exist_ok=True)
    stop_file = ROOT / 'STOP_REQUESTED'
    stop_file.unlink(missing_ok=True)
    base_url = cfg['base_url']
    selectors = cfg['selectors']
    timeout = int(cfg.get('timeout_ms', 12000))
    rows = []
    start = time.time()
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=['--no-sandbox'])
        context = await browser.new_context()
        page = await context.new_page()
        page.set_default_timeout(timeout)
        try:
            emit_event({"time": stamp(), "status":"info", "message":"فتح صفحة تسجيل الدخول"})
            await page.goto(base_url, wait_until='domcontentloaded')
            # Credentials are only filled into configured login fields; never logged or saved.
            await page.locator(selectors['username']).fill(username)
            await page.locator(selectors['password']).fill(password)
            await page.locator(selectors['login_submit']).click()
            await page.wait_for_load_state('domcontentloaded')
            if selectors.get('post_login_ready'):
                await page.locator(selectors['post_login_ready']).wait_for(state='visible')
            emit_event({"time":stamp(), "status":"success", "message":"تم تسجيل الدخول لحساب الاختبار"})
            fixtures = answer_data.get('questions', [])
            if not fixtures:
                raise ValueError('test_answers.json لا يحتوي على أسئلة اختبار. أضف إجابات معروفة لبيئة الاختبار.')
            for index, fixture in enumerate(fixtures, start=1):
                if stop_file.exists():
                    emit_event({"time":stamp(), "status":"warning", "message":"تم إيقاف الاختبار بطلب المستخدم"})
                    break
                qloc = page.locator(selectors['question']).first
                await qloc.wait_for(state='visible')
                qtext = (await qloc.inner_text()).strip()
                if not qtext:
                    qtext = f'Question {index} (text not detected)'
                expected_match = fixture.get('question_contains')
                if expected_match and text_norm(expected_match) not in text_norm(qtext):
                    row = {"chapter": fixture.get('chapter',''), "index": index, "question": qtext[:500], "expected_question_contains": expected_match, "answer": fixture.get('answer_text',''), "result":"needs_review", "details":"نص السؤال لا يطابق السؤال المتوقع في ملف الاختبار؛ لم يتم الإرسال."}
                    rows.append(row); emit_event({"time":stamp(), "status":"warning", "message":f'السؤال {index}: لم يطابق نص السؤال المتوقع، تم تخطي الإرسال', "row":row}); continue
                answer_text = str(fixture['answer_text'])
                options = page.locator(selectors['option'])
                count = await options.count()
                chosen = None
                for oi in range(count):
                    opt = options.nth(oi)
                    label = (await opt.inner_text()).strip()
                    aria = await opt.get_attribute('aria-label') or ''
                    value = await opt.get_attribute('value') or ''
                    if text_norm(answer_text) in {text_norm(label), text_norm(aria), text_norm(value)}:
                        chosen = opt; break
                if chosen is None:
                    row = {"chapter":fixture.get('chapter',''),"index":index,"question":qtext[:500],"answer":answer_text,"result":"failed","details":f'لم يتم العثور على اختيار مطابق؛ عدد الخيارات المرئية: {count}'}
                    rows.append(row); emit_event({"time":stamp(),"status":"error","message":f'السؤال {index}: لم يتم العثور على الاختيار المحدد',"row":row}); continue
                await chosen.click()
                await page.locator(selectors['submit']).click()
                outcome = 'unknown'
                details = 'لم يظهر مؤشر واضح للصواب أو الخطأ خلال المهلة.'
                try:
                    await page.locator(f"{selectors['correct_indicator']}, {selectors['incorrect_indicator']}").first.wait_for(state='visible', timeout=int(cfg.get('feedback_timeout_ms', 4000)))
                except PlaywrightTimeoutError:
                    pass
                if await page.locator(selectors['correct_indicator']).count() and await page.locator(selectors['correct_indicator']).first.is_visible():
                    outcome, details = 'passed', 'المنصة عرضت مؤشر الإجابة الصحيحة.'
                elif await page.locator(selectors['incorrect_indicator']).count() and await page.locator(selectors['incorrect_indicator']).first.is_visible():
                    outcome, details = 'failed', 'المنصة عرضت مؤشر الإجابة الخاطئة.'
                # Wait for the next item to unlock/change; no blind click if no next control.
                next_sel = selectors.get('next')
                if index < len(fixtures) and next_sel and await page.locator(next_sel).count() and await page.locator(next_sel).first.is_visible() and await page.locator(next_sel).first.is_enabled():
                    old_text = qtext
                    await page.locator(next_sel).first.click()
                    try:
                        await page.wait_for_function("(arg) => { const e=document.querySelector(arg.sel); return !!e && (e.innerText||'').trim() !== arg.old; }", arg={"sel": selectors['question'], "old": old_text}, timeout=int(cfg.get('next_timeout_ms', 8000)))
                        details += ' تم الانتقال للسؤال التالي.'
                    except PlaywrightTimeoutError:
                        details += ' لم يتغير نص السؤال بعد الضغط على التالي؛ يحتاج مراجعة.'
                        if outcome == 'passed': outcome = 'needs_review'
                elif index < len(fixtures):
                    details += ' لا يوجد زر التالي متاح؛ قد يكون السؤال التالي مقفولًا أو أن محدد الزر يحتاج ضبطًا.'
                    if outcome == 'passed': outcome = 'needs_review'
                row = {"chapter":fixture.get('chapter',''),"index":index,"question":qtext[:500],"answer":answer_text,"result":outcome,"details":details}
                rows.append(row)
                emit_event({"time":stamp(),"status":'success' if outcome=='passed' else ('error' if outcome=='failed' else 'warning'),"message":f'السؤال {index}: {outcome} — {details}',"row":row})
            report = {"started_at":datetime.fromtimestamp(start, timezone.utc).isoformat(timespec='seconds'),"finished_at":stamp(),"target":base_url,"total":len(rows),"passed":sum(r['result']=='passed' for r in rows),"failed":sum(r['result']=='failed' for r in rows),"needs_review":sum(r['result']=='needs_review' for r in rows),"rows":rows}
            (OUTPUT/'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
            with (OUTPUT/'report.csv').open('w', newline='', encoding='utf-8-sig') as f:
                writer=csv.DictWriter(f, fieldnames=['chapter','index','question','answer','result','details']); writer.writeheader(); writer.writerows(rows)
            emit_event({"time":stamp(),"status":"success","message":f"اكتمل التقرير: {report['passed']} ناجح، {report['failed']} فاشل، {report['needs_review']} يحتاج مراجعة."})
            return report
        finally:
            await context.close(); await browser.close()
