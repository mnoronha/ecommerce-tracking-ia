"""
Tests for app/core/meeting_analysis.py

Coverage:
  - MeetingAnalysisUnavailable raised when Claude not available
  - analyze_transcript returns all required sections
  - No invented owners (null when not in transcript)
  - No invented dates (null when not in transcript; non-ISO stripped)
  - requires_human_review=True on all action items
  - to_process_payload maps action_items → commitments correctly
  - _empty_result returned on JSON parse failure
  - _strip_fences handles markdown code fences
  - _validate_no_invented_dates strips non-ISO strings
  - Meeting context: load_meeting_context returns well-formed output
  - format_for_hermes produces non-empty text
"""
import json
from datetime import date
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest


# ── Helpers ────────────────────────────────────────────────────────────────────

def _mock_claude_response(payload: dict) -> MagicMock:
    """Return a mock Anthropic messages.create response with given payload as JSON."""
    msg = MagicMock()
    msg.content = [SimpleNamespace(text=json.dumps(payload))]
    return msg


def _full_analysis_payload(
    *,
    summary: str = "Resumo da reunião.",
    decisions=None,
    action_items=None,
    risks=None,
    open_questions=None,
    follow_ups=None,
) -> dict:
    return {
        "summary": summary,
        "decisions": decisions or [{"decision": "Aprovar budget", "owner": "João"}],
        "action_items": action_items or [
            {
                "task": "Criar relatório semanal",
                "owner": "Maria",
                "due_date": "2026-11-10",
                "priority": "HIGH",
                "source_quote": "Maria vai criar o relatório até segunda",
                "requires_human_review": True,
            }
        ],
        "risks": risks or [{"risk": "Prazo apertado", "severity": "MEDIUM"}],
        "open_questions": open_questions or ["Qual o budget aprovado?"],
        "follow_ups": follow_ups or [{"action": "Revisar deck", "owner": "João"}],
    }


# ── MeetingAnalysisUnavailable ─────────────────────────────────────────────────

class TestAnalysisUnavailable:
    def test_raises_when_no_api_key(self):
        from app.core.meeting_analysis import MeetingAnalysisUnavailable, analyze_transcript
        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "", "ANTHROPIC_CREDITS_OK": ""}, clear=False):
            with pytest.raises(MeetingAnalysisUnavailable) as exc:
                analyze_transcript("some transcript", "Test Meeting")
            assert exc.value.reason == "UNAVAILABLE_NO_KEY"

    def test_raises_when_credits_blocked(self):
        from app.core.meeting_analysis import MeetingAnalysisUnavailable, analyze_transcript
        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "sk-test", "ANTHROPIC_CREDITS_OK": ""}, clear=False):
            with pytest.raises(MeetingAnalysisUnavailable) as exc:
                analyze_transcript("some transcript", "Test Meeting")
            assert exc.value.reason == "DEGRADED_BLOCKED_CREDITS"

    def test_raises_on_empty_transcript(self):
        from app.core.meeting_analysis import analyze_transcript
        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "sk-test", "ANTHROPIC_CREDITS_OK": "true"}, clear=False):
            with patch("anthropic.Anthropic"):
                with pytest.raises(ValueError, match="transcript_text is required"):
                    analyze_transcript("   ", "Test Meeting")


# ── Full analysis output ────────────────────────────────────────────────────────

class TestAnalyzeTranscript:
    @pytest.fixture
    def env_ok(self):
        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "sk-test", "ANTHROPIC_CREDITS_OK": "true"}):
            yield

    def _run(self, payload: dict, transcript: str = "João: Vamos aprovar o budget.\nMaria: Ok, aprovado."):
        with patch("anthropic.Anthropic") as MockClient:
            MockClient.return_value.messages.create.return_value = _mock_claude_response(payload)
            from app.core import meeting_analysis as ma
            # reload to avoid import cache issues with patching
            return ma.analyze_transcript(transcript, "Sprint Planning", "2026-11-05")

    def test_returns_summary(self, env_ok):
        result = self._run(_full_analysis_payload(summary="Reunião produtiva."))
        assert result["summary"] == "Reunião produtiva."

    def test_returns_decisions(self, env_ok):
        result = self._run(_full_analysis_payload())
        assert len(result["decisions"]) == 1
        assert result["decisions"][0]["decision"] == "Aprovar budget"

    def test_returns_action_items(self, env_ok):
        result = self._run(_full_analysis_payload())
        assert len(result["action_items"]) == 1
        item = result["action_items"][0]
        assert item["task"] == "Criar relatório semanal"
        assert item["requires_human_review"] is True

    def test_returns_risks(self, env_ok):
        result = self._run(_full_analysis_payload())
        assert len(result["risks"]) == 1
        assert result["risks"][0]["risk"] == "Prazo apertado"

    def test_returns_open_questions(self, env_ok):
        result = self._run(_full_analysis_payload())
        assert "Qual o budget aprovado?" in result["open_questions"]

    def test_returns_follow_ups(self, env_ok):
        result = self._run(_full_analysis_payload())
        assert len(result["follow_ups"]) == 1


# ── No invented owners ─────────────────────────────────────────────────────────

class TestNoInventedOwners:
    @pytest.fixture
    def env_ok(self):
        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "sk-test", "ANTHROPIC_CREDITS_OK": "true"}):
            yield

    def test_owner_null_preserved(self, env_ok):
        payload = _full_analysis_payload(
            action_items=[{"task": "Fazer algo", "owner": None, "due_date": None, "priority": "LOW", "source_quote": "Alguém vai fazer algo"}]
        )
        with patch("anthropic.Anthropic") as MockClient:
            MockClient.return_value.messages.create.return_value = _mock_claude_response(payload)
            from app.core import meeting_analysis as ma
            result = ma.analyze_transcript("Texto", "Test")
        assert result["action_items"][0]["owner"] is None

    def test_owner_name_preserved(self, env_ok):
        payload = _full_analysis_payload(
            action_items=[{"task": "Revisar", "owner": "Pedro", "due_date": None, "priority": "MEDIUM", "source_quote": "Pedro vai revisar"}]
        )
        with patch("anthropic.Anthropic") as MockClient:
            MockClient.return_value.messages.create.return_value = _mock_claude_response(payload)
            from app.core import meeting_analysis as ma
            result = ma.analyze_transcript("Texto", "Test")
        assert result["action_items"][0]["owner"] == "Pedro"


# ── No invented dates ──────────────────────────────────────────────────────────

class TestNoInventedDates:
    @pytest.fixture
    def env_ok(self):
        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "sk-test", "ANTHROPIC_CREDITS_OK": "true"}):
            yield

    def test_valid_iso_date_preserved(self, env_ok):
        payload = _full_analysis_payload(
            action_items=[{"task": "X", "owner": None, "due_date": "2026-11-15", "priority": "MEDIUM", "source_quote": "até dia 15"}]
        )
        with patch("anthropic.Anthropic") as MockClient:
            MockClient.return_value.messages.create.return_value = _mock_claude_response(payload)
            from app.core import meeting_analysis as ma
            result = ma.analyze_transcript("Texto", "Test")
        assert result["action_items"][0]["due_date"] == "2026-11-15"

    def test_non_iso_date_stripped(self, env_ok):
        payload = _full_analysis_payload(
            action_items=[{"task": "X", "owner": None, "due_date": "próxima semana", "priority": "MEDIUM", "source_quote": "X"}]
        )
        with patch("anthropic.Anthropic") as MockClient:
            MockClient.return_value.messages.create.return_value = _mock_claude_response(payload)
            from app.core import meeting_analysis as ma
            result = ma.analyze_transcript("Texto", "Test")
        assert result["action_items"][0]["due_date"] is None

    def test_null_date_preserved(self, env_ok):
        payload = _full_analysis_payload(
            action_items=[{"task": "X", "owner": None, "due_date": None, "priority": "MEDIUM", "source_quote": "X"}]
        )
        with patch("anthropic.Anthropic") as MockClient:
            MockClient.return_value.messages.create.return_value = _mock_claude_response(payload)
            from app.core import meeting_analysis as ma
            result = ma.analyze_transcript("Texto", "Test")
        assert result["action_items"][0]["due_date"] is None

    def test_follow_up_non_iso_stripped(self, env_ok):
        from app.core.meeting_analysis import _validate_no_invented_dates
        items = [{"action": "Review", "owner": None, "due_date": "Monday"}]
        result = _validate_no_invented_dates(items)
        assert result[0]["due_date"] is None

    def test_follow_up_iso_preserved(self):
        from app.core.meeting_analysis import _validate_no_invented_dates
        items = [{"action": "Review", "owner": None, "due_date": "2026-11-10"}]
        result = _validate_no_invented_dates(items)
        assert result[0]["due_date"] == "2026-11-10"


# ── requires_human_review ──────────────────────────────────────────────────────

class TestRequiresHumanReview:
    @pytest.fixture
    def env_ok(self):
        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "sk-test", "ANTHROPIC_CREDITS_OK": "true"}):
            yield

    def test_always_true_even_if_claude_says_false(self, env_ok):
        payload = _full_analysis_payload(
            action_items=[{"task": "Deploy", "owner": "Dev", "due_date": None, "priority": "HIGH", "source_quote": "X", "requires_human_review": False}]
        )
        with patch("anthropic.Anthropic") as MockClient:
            MockClient.return_value.messages.create.return_value = _mock_claude_response(payload)
            from app.core import meeting_analysis as ma
            result = ma.analyze_transcript("Texto", "Test")
        assert result["action_items"][0]["requires_human_review"] is True

    def test_always_true_even_if_claude_omits_field(self, env_ok):
        payload = _full_analysis_payload(
            action_items=[{"task": "Deploy", "owner": None, "due_date": None, "priority": "MEDIUM", "source_quote": "X"}]
        )
        with patch("anthropic.Anthropic") as MockClient:
            MockClient.return_value.messages.create.return_value = _mock_claude_response(payload)
            from app.core import meeting_analysis as ma
            result = ma.analyze_transcript("Texto", "Test")
        assert result["action_items"][0]["requires_human_review"] is True


# ── _strip_fences ──────────────────────────────────────────────────────────────

class TestStripFences:
    def test_strips_json_fence(self):
        from app.core.meeting_analysis import _strip_fences
        raw = "```json\n{\"foo\": 1}\n```"
        assert _strip_fences(raw) == '{"foo": 1}'

    def test_strips_plain_fence(self):
        from app.core.meeting_analysis import _strip_fences
        raw = "```\n{\"foo\": 1}\n```"
        assert _strip_fences(raw) == '{"foo": 1}'

    def test_no_fence(self):
        from app.core.meeting_analysis import _strip_fences
        raw = '{"foo": 1}'
        assert _strip_fences(raw) == '{"foo": 1}'


# ── _empty_result on bad JSON ──────────────────────────────────────────────────

class TestEmptyResultOnBadJson:
    @pytest.fixture
    def env_ok(self):
        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "sk-test", "ANTHROPIC_CREDITS_OK": "true"}):
            yield

    def test_returns_empty_on_invalid_json(self, env_ok):
        with patch("anthropic.Anthropic") as MockClient:
            msg = MagicMock()
            msg.content = [SimpleNamespace(text="this is not json at all")]
            MockClient.return_value.messages.create.return_value = msg
            from app.core import meeting_analysis as ma
            result = ma.analyze_transcript("some text", "Test")
        assert result["summary"] is None
        assert result["decisions"] == []
        assert result["action_items"] == []
        assert result["risks"] == []


# ── to_process_payload ─────────────────────────────────────────────────────────

class TestToProcessPayload:
    def test_maps_action_items_to_commitments(self):
        from app.core.meeting_analysis import to_process_payload
        analysis = {
            "summary": "Summary",
            "decisions": [],
            "action_items": [{"task": "Build feature", "owner": "Ana", "due_date": "2026-11-20", "priority": "HIGH", "source_quote": "X", "requires_human_review": True}],
            "risks": [],
            "open_questions": [],
            "follow_ups": [],
        }
        payload = to_process_payload(analysis)
        assert len(payload["commitments"]) == 1
        c = payload["commitments"][0]
        assert c["what"] == "Build feature"
        assert c["who"] == "Ana"
        assert c["due_date"] == "2026-11-20"

    def test_processor_id_is_set(self):
        from app.core.meeting_analysis import to_process_payload, PROCESSOR_ID
        payload = to_process_payload({"summary": None, "decisions": [], "action_items": [], "risks": [], "open_questions": [], "follow_ups": []})
        assert payload["processor"] == PROCESSOR_ID

    def test_create_tasks_false_by_default(self):
        from app.core.meeting_analysis import to_process_payload
        payload = to_process_payload({"summary": None, "decisions": [], "action_items": [], "risks": [], "open_questions": [], "follow_ups": []})
        assert payload["create_tasks"] is False

    def test_risks_passed_through(self):
        from app.core.meeting_analysis import to_process_payload
        analysis = {
            "summary": None,
            "decisions": [],
            "action_items": [],
            "risks": [{"risk": "Risco X", "severity": "HIGH"}],
            "open_questions": [],
            "follow_ups": [],
        }
        payload = to_process_payload(analysis)
        assert len(payload["risks"]) == 1
        assert payload["risks"][0]["risk"] == "Risco X"


# ── Meeting context ────────────────────────────────────────────────────────────

class TestMeetingContext:
    def _make_db(self, meetings=None, summaries=None):
        db = MagicMock()
        m_chain = MagicMock()
        m_chain.execute.return_value.data = meetings or []
        db.table.return_value.select.return_value.eq.return_value.order.return_value.limit.return_value = m_chain

        s_chain = MagicMock()
        s_chain.execute.return_value.data = summaries or []
        # Second call for summaries
        db.table.return_value.select.return_value.in_.return_value = s_chain

        return db

    def test_empty_when_no_meetings(self):
        from app.core.meeting_context import load_meeting_context
        with patch("app.core.meeting_context.get_supabase") as mock_sb:
            db = MagicMock()
            db.table.return_value.select.return_value.eq.return_value.order.return_value.limit.return_value.execute.return_value.data = []
            mock_sb.return_value = db
            ctx = load_meeting_context("test-client")
        assert ctx["total_meetings"] == 0
        assert ctx["recent_meetings"] == []
        assert ctx["recent_decisions"] == []
        assert ctx["open_action_items"] == []

    def test_returns_meeting_data(self):
        from app.core.meeting_context import load_meeting_context
        meetings = [{"id": "m1", "title": "Sprint Review", "started_at": "2026-11-01T10:00:00", "transcript_status": "complete", "processing_status": "done"}]
        summaries = [{"meeting_id": "m1", "summary": "Boa reunião", "decisions": json.dumps([{"decision": "Deploy on Friday"}]), "commitments": json.dumps([{"what": "Fix bug", "who": "Pedro"}]), "open_questions": json.dumps(["When?"]), "follow_ups": json.dumps([]), "risks": json.dumps([])}]

        with patch("app.core.meeting_context.get_supabase") as mock_sb:
            db = MagicMock()
            meetings_q = MagicMock()
            meetings_q.execute.return_value.data = meetings
            db.table.return_value.select.return_value.eq.return_value.order.return_value.limit.return_value = meetings_q

            summaries_q = MagicMock()
            summaries_q.execute.return_value.data = summaries
            db.table.return_value.select.return_value.in_.return_value = summaries_q

            mock_sb.return_value = db
            ctx = load_meeting_context("lk-sneakers")

        assert ctx["total_meetings"] == 1
        assert ctx["recent_meetings"][0]["title"] == "Sprint Review"
        assert ctx["recent_decisions"][0]["decision"] == "Deploy on Friday"
        assert ctx["open_action_items"][0]["task"] == "Fix bug"
        assert "When?" in ctx["open_questions"]

    def test_format_for_hermes_empty(self):
        from app.core.meeting_context import format_for_hermes, _empty_context
        text = format_for_hermes(_empty_context())
        assert "Nenhuma reunião" in text

    def test_format_for_hermes_with_data(self):
        from app.core.meeting_context import format_for_hermes
        ctx = {
            "recent_meetings": [{"id": "m1", "title": "Planning", "started_at": "2026-11-01T10:00:00", "processing_status": "done", "summary": "Boa reunião"}],
            "recent_decisions": [{"decision": "Aprovar budget", "owner": "Ana", "meeting_id": "m1", "meeting_title": "Planning", "meeting_date": "2026-11-01"}],
            "open_action_items": [{"task": "Enviar proposta", "owner": "Pedro", "due_date": "2026-11-10", "priority": "HIGH", "meeting_id": "m1", "meeting_title": "Planning"}],
            "open_questions": ["Qual é o prazo?"],
            "total_meetings": 1,
        }
        text = format_for_hermes(ctx)
        assert "REUNIÕES RECENTES" in text
        assert "Planning" in text
        assert "Aprovar budget" in text
        assert "Enviar proposta" in text


# ── LK Sneakers Pilot ─────────────────────────────────────────────────────────

_LK_FIXTURE_TRANSCRIPT = """\
Maico: Bom dia pessoal. Vamos alinhar a campanha de Black Friday da LK Sneakers.
Maria (Account): Temos três campanhas rodando: Retargeting, Prospecting, e Branding.
Maico: O ROAS do Retargeting caiu 15% semana passada. O que houve?
Pedro (Media): Aumentamos o CPM porque expandimos o público. Precisamos reverter o ajuste de audience.
Maico: Ok, vamos reverter. Pedro, consegue fazer isso hoje?
Pedro (Media): Sim, faço ainda hoje.
Maria (Account): Precisa também criar uma campanha nova para o lançamento do tênis Air LK em dezembro.
Maico: Qual o budget aprovado para o lançamento?
Maria (Account): R$ 15.000, mas ainda precisa passar pela diretoria.
Maico: Então aguardamos confirmação da diretoria antes de criar. Maria, você acompanha isso?
Maria (Account): Sim, falo com eles ainda essa semana.
Maico: Riscos que vejo: o prazo do Air LK é apertado, se a diretoria não aprovar até sexta-feira não conseguimos configurar a campanha a tempo.
Pedro (Media): Concordo. Também tem o risco de sazonalidade — a Black Friday pode canibalizar o lançamento.
Maico: Bom ponto. Alguma questão aberta?
Maria (Account): Qual será a landing page do Air LK? Não foi definida ainda.
Maico: Isso precisa estar definido antes de criar a campanha. Coloco como pendência.
"""

_LK_CLAUDE_RESPONSE = {
    "summary": "Alinhamento de campanha Black Friday LK Sneakers: queda de ROAS em Retargeting, decisão de reverter ajuste de audience, pendência de aprovação de budget para lançamento Air LK.",
    "decisions": [
        {"decision": "Reverter ajuste de audience no Retargeting", "owner": "Pedro", "rationale": "ROAS caiu 15% após expansão de público"},
        {"decision": "Aguardar aprovação da diretoria antes de criar campanha Air LK", "owner": "Maria", "rationale": "Budget de R$15.000 ainda pendente"}
    ],
    "action_items": [
        {"task": "Reverter audience Retargeting", "owner": "Pedro", "due_date": "2026-11-05", "priority": "HIGH", "source_quote": "faço ainda hoje", "requires_human_review": True},
        {"task": "Acompanhar aprovação de budget Air LK com diretoria", "owner": "Maria", "due_date": None, "priority": "HIGH", "source_quote": "falo com eles ainda essa semana", "requires_human_review": True},
        {"task": "Definir landing page do Air LK", "owner": None, "due_date": None, "priority": "MEDIUM", "source_quote": "Qual será a landing page do Air LK? Não foi definida ainda.", "requires_human_review": True}
    ],
    "risks": [
        {"risk": "Prazo apertado para Air LK — aprovação necessária até sexta-feira", "severity": "HIGH", "mitigation_suggested": "Escalar decisão para diretoria com urgência"},
        {"risk": "Black Friday pode canibalizar lançamento do Air LK", "severity": "MEDIUM", "mitigation_suggested": None}
    ],
    "open_questions": ["Qual será a landing page do Air LK?", "Budget de R$15.000 foi aprovado pela diretoria?"],
    "follow_ups": [
        {"action": "Confirmar aprovação de budget", "owner": "Maria", "due_date": None}
    ]
}


class TestLKSneakersPilot:
    """End-to-end fixture test with a realistic LK Sneakers meeting transcript."""

    @pytest.fixture
    def env_ok(self):
        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "sk-test", "ANTHROPIC_CREDITS_OK": "true"}):
            yield

    def _run_lk(self):
        with patch("anthropic.Anthropic") as MockClient:
            MockClient.return_value.messages.create.return_value = _mock_claude_response(_LK_CLAUDE_RESPONSE)
            from app.core import meeting_analysis as ma
            return ma.analyze_transcript(_LK_FIXTURE_TRANSCRIPT, "Black Friday — LK Sneakers", "2026-11-05")

    def test_lk_summary_extracted(self, env_ok):
        result = self._run_lk()
        assert result["summary"] is not None
        assert len(result["summary"]) > 20

    def test_lk_two_decisions(self, env_ok):
        result = self._run_lk()
        assert len(result["decisions"]) == 2
        texts = [d["decision"] for d in result["decisions"]]
        assert any("Retargeting" in t for t in texts)

    def test_lk_three_action_items(self, env_ok):
        result = self._run_lk()
        assert len(result["action_items"]) == 3

    def test_lk_pedro_owns_reverter(self, env_ok):
        result = self._run_lk()
        reverter = next(a for a in result["action_items"] if "Retargeting" in a["task"])
        assert reverter["owner"] == "Pedro"

    def test_lk_landing_page_owner_null(self, env_ok):
        result = self._run_lk()
        landing = next(a for a in result["action_items"] if "landing" in a["task"].lower())
        assert landing["owner"] is None

    def test_lk_all_action_items_require_review(self, env_ok):
        result = self._run_lk()
        assert all(a["requires_human_review"] for a in result["action_items"])

    def test_lk_two_risks(self, env_ok):
        result = self._run_lk()
        assert len(result["risks"]) == 2
        severities = {r["severity"] for r in result["risks"]}
        assert "HIGH" in severities

    def test_lk_open_questions(self, env_ok):
        result = self._run_lk()
        assert len(result["open_questions"]) >= 1

    def test_lk_to_process_payload_shape(self, env_ok):
        result = self._run_lk()
        from app.core.meeting_analysis import to_process_payload, PROCESSOR_ID
        payload = to_process_payload(result)
        assert payload["processor"] == PROCESSOR_ID
        assert len(payload["commitments"]) == 3
        assert len(payload["risks"]) == 2
        assert payload["create_tasks"] is False

    def test_lk_commitments_map_owner_correctly(self, env_ok):
        result = self._run_lk()
        from app.core.meeting_analysis import to_process_payload
        payload = to_process_payload(result)
        pedro_task = next(c for c in payload["commitments"] if c["who"] == "Pedro")
        assert "Retargeting" in pedro_task["what"]
