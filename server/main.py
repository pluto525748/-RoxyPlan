from __future__ import annotations

import ipaddress
import os
import secrets
from pathlib import Path
from threading import Lock
from typing import Callable, List, Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, Response, status
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from server.agent_service import AgentService
from server.schemas import (
    ActionCreateSchema,
    AgentRequestSchema,
    AgentResponseSchema,
    CandidateDecisionSchema,
    CandidateEditedAcceptSchema,
    ConfirmationId,
    ConflictResolutionSchema,
    ConversationCreateSchema,
    ConversationDetailSchema,
    ConversationId,
    ConversationRenameSchema,
    ConversationSummarySchema,
    HealthResponseSchema,
    PlanCreateSchema,
    ServiceStatusSchema,
    ToolResultSchema,
)


WEB_DIR = Path(__file__).resolve().parent / "web"
TOKEN_HEADER = "X-Roxy-Token"


def is_loopback_host(host: str) -> bool:
    value = str(host or "").strip().split("%", 1)[0]
    if value.lower() == "localhost":
        return True
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return False
    if address.is_loopback:
        return True
    mapped = getattr(address, "ipv4_mapped", None)
    return bool(mapped and mapped.is_loopback)


def require_local_access(
    request: Request,
    roxy_token: Optional[str] = Header(default=None, alias=TOKEN_HEADER),
) -> None:
    configured_token = os.environ.get("ROXY_LOCAL_TOKEN", "").strip()
    if configured_token:
        supplied = str(roxy_token or "")
        if not secrets.compare_digest(supplied, configured_token):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="访问令牌不正确。",
            )
        return

    host = request.client.host if request.client is not None else ""
    if not is_loopback_host(host):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="未配置 ROXY_LOCAL_TOKEN 时仅允许本机访问。",
        )


def create_app(
    agent_service: Optional[AgentService] = None,
    service_factory: Callable[[], AgentService] = AgentService,
) -> FastAPI:
    application = FastAPI(
        title="RoxyPlan Local Web",
        version="0.1",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    application.mount("/static", StaticFiles(directory=str(WEB_DIR)), name="static")
    application.state.agent_service = agent_service
    application.state.service_factory = service_factory
    application.state.service_lock = Lock()

    def get_agent_service() -> AgentService:
        service = application.state.agent_service
        if service is not None:
            return service
        with application.state.service_lock:
            service = application.state.agent_service
            if service is None:
                service = application.state.service_factory()
                application.state.agent_service = service
        return service

    def tool_response(result) -> ToolResultSchema:
        return ToolResultSchema.parse_obj(result.to_dict())

    @application.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(str(WEB_DIR / "index.html"))

    @application.get("/health", response_model=HealthResponseSchema)
    def health() -> HealthResponseSchema:
        return HealthResponseSchema(status="ok", service="roxy-local-web")

    @application.get(
        "/v1/status",
        response_model=ServiceStatusSchema,
        dependencies=[Depends(require_local_access)],
    )
    def service_status() -> ServiceStatusSchema:
        return ServiceStatusSchema.parse_obj(get_agent_service().status())

    @application.get(
        "/v1/model-status",
        dependencies=[Depends(require_local_access)],
    )
    def model_status():
        return get_agent_service().model_status()

    @application.get(
        "/v1/personas",
        dependencies=[Depends(require_local_access)],
    )
    def list_personas():
        return {
            "active_persona_id": get_agent_service().persona_registry.active_persona_id,
            "personas": get_agent_service().available_personas(),
        }

    @application.get(
        "/v1/model-usage",
        dependencies=[Depends(require_local_access)],
    )
    def model_usage():
        return get_agent_service().model_usage()

    @application.delete(
        "/v1/model-usage",
        dependencies=[Depends(require_local_access)],
    )
    def clear_model_usage():
        return {"cleared": get_agent_service().clear_model_usage()}

    @application.post(
        "/v1/agent/requests",
        response_model=AgentResponseSchema,
        dependencies=[Depends(require_local_access)],
    )
    def agent_request(payload: AgentRequestSchema, response: Response) -> AgentResponseSchema:
        try:
            result = get_agent_service().handle(
                payload.message,
                payload.conversation_id,
            )
        except Exception as error:
            print(f"[LocalWeb] request failed: {type(error).__name__}", flush=True)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Roxy 暂时无法处理这条消息，请查看电脑端控制台日志。",
            )
        response.headers["Cache-Control"] = "no-store"
        return AgentResponseSchema.parse_obj(result.to_dict())

    @application.get(
        "/v1/plans",
        response_model=ToolResultSchema,
        dependencies=[Depends(require_local_access)],
    )
    def list_plans() -> ToolResultSchema:
        return tool_response(get_agent_service().execute_tool("show_plan"))

    @application.post(
        "/v1/plans",
        response_model=ToolResultSchema,
        dependencies=[Depends(require_local_access)],
    )
    def add_plan(payload: PlanCreateSchema) -> ToolResultSchema:
        return tool_response(
            get_agent_service().execute_tool("add_plan", {"title": payload.title})
        )

    @application.post(
        "/v1/plans/{task_id}/complete",
        response_model=ToolResultSchema,
        dependencies=[Depends(require_local_access)],
    )
    def complete_plan(task_id: int) -> ToolResultSchema:
        return tool_response(
            get_agent_service().execute_tool(
                "complete_plan",
                {"match_text": str(task_id)},
            )
        )

    @application.delete(
        "/v1/plans/{task_id}",
        response_model=ToolResultSchema,
        dependencies=[Depends(require_local_access)],
    )
    def delete_plan(task_id: int) -> ToolResultSchema:
        return tool_response(
            get_agent_service().execute_tool(
                "delete_plan",
                {"task_ref": str(task_id)},
            )
        )

    @application.post(
        "/v1/confirmations/{confirmation_id}",
        response_model=ToolResultSchema,
        dependencies=[Depends(require_local_access)],
    )
    def confirm_operation(confirmation_id: ConfirmationId) -> ToolResultSchema:
        return tool_response(get_agent_service().confirm_tool(confirmation_id))

    @application.get(
        "/v1/actions",
        response_model=ToolResultSchema,
        dependencies=[Depends(require_local_access)],
    )
    def list_actions() -> ToolResultSchema:
        return tool_response(get_agent_service().execute_tool("show_action_log"))

    @application.post(
        "/v1/actions",
        response_model=ToolResultSchema,
        dependencies=[Depends(require_local_access)],
    )
    def add_action(payload: ActionCreateSchema) -> ToolResultSchema:
        return tool_response(
            get_agent_service().execute_tool(
                "add_action_log",
                {"content": payload.content},
            )
        )

    @application.get(
        "/v1/growth/review",
        response_model=ToolResultSchema,
        dependencies=[Depends(require_local_access)],
    )
    def get_review() -> ToolResultSchema:
        return tool_response(
            get_agent_service().execute_tool("generate_daily_review")
        )

    @application.post(
        "/v1/growth/review/save",
        response_model=ToolResultSchema,
        dependencies=[Depends(require_local_access)],
    )
    def save_review() -> ToolResultSchema:
        return tool_response(
            get_agent_service().execute_tool("save_daily_review")
        )

    @application.get(
        "/v1/growth/logs",
        response_model=ToolResultSchema,
        dependencies=[Depends(require_local_access)],
    )
    def list_growth_logs() -> ToolResultSchema:
        return tool_response(get_agent_service().execute_tool("show_growth_log"))

    @application.get(
        "/v1/memories",
        response_model=ToolResultSchema,
        dependencies=[Depends(require_local_access)],
    )
    def list_memories(
        query: str = Query(default="", max_length=240),
    ) -> ToolResultSchema:
        return tool_response(get_agent_service().memory_view(query))

    @application.get(
        "/v1/memory-candidates",
        dependencies=[Depends(require_local_access)],
    )
    def list_memory_candidates():
        return get_agent_service().memory_candidates_view()

    @application.post(
        "/v1/memory-candidates/reject-low-value",
        dependencies=[Depends(require_local_access)],
    )
    def reject_low_value_memory_candidates(payload: CandidateDecisionSchema):
        if not payload.confirmed:
            raise HTTPException(status_code=409, detail="需要确认批量拒绝低价值候选。")
        return get_agent_service().reject_low_value_candidates()

    @application.post(
        "/v1/memory-candidates/{candidate_id}/accept",
        dependencies=[Depends(require_local_access)],
    )
    def accept_memory_candidate(candidate_id: int, payload: CandidateDecisionSchema):
        candidate_result = get_agent_service().memory_service.get_candidate(candidate_id)
        candidate = candidate_result.data.get("candidate", {})
        if candidate is None or candidate.get("status") != "pending":
            raise HTTPException(status_code=404, detail="没有找到待审核候选。")
        if not payload.confirmed:
            raise HTTPException(
                status_code=409,
                detail={
                    "message": "接受前需要确认最终写入内容。",
                    "final_content": str(candidate.get("content", "")),
                    "sensitivity": str(candidate.get("sensitivity", "normal")),
                },
            )
        return get_agent_service().accept_memory_candidate(candidate_id)

    @application.post(
        "/v1/memory-candidates/{candidate_id}/reject",
        dependencies=[Depends(require_local_access)],
    )
    def reject_memory_candidate(candidate_id: int):
        result = get_agent_service().reject_memory_candidate(candidate_id)
        if result.get("status") == "missing":
            raise HTTPException(status_code=404, detail="没有找到待审核候选。")
        return result

    @application.post(
        "/v1/memory-candidates/{candidate_id}/accept-edited",
        dependencies=[Depends(require_local_access)],
    )
    def accept_edited_memory_candidate(
        candidate_id: int,
        payload: CandidateEditedAcceptSchema,
    ):
        candidate_result = get_agent_service().memory_service.get_candidate(candidate_id)
        candidate = candidate_result.data.get("candidate", {})
        if candidate is None or candidate.get("status") != "pending":
            raise HTTPException(status_code=404, detail="没有找到待审核候选。")
        if not payload.confirmed:
            raise HTTPException(
                status_code=409,
                detail={
                    "message": "接受前需要确认编辑后的最终内容。",
                    "final_content": payload.content,
                    "sensitivity": str(candidate.get("sensitivity", "normal")),
                },
            )
        return get_agent_service().accept_memory_candidate(
            candidate_id,
            edited_content=payload.content,
        )

    @application.get(
        "/v1/memory-conflicts",
        dependencies=[Depends(require_local_access)],
    )
    def list_memory_conflicts():
        return get_agent_service().memory_conflicts_view()

    @application.post(
        "/v1/memory-conflicts/{conflict_id}/resolve",
        dependencies=[Depends(require_local_access)],
    )
    def resolve_memory_conflict(
        conflict_id: int,
        payload: ConflictResolutionSchema,
    ):
        if not payload.confirmed:
            raise HTTPException(status_code=409, detail="记忆冲突处理需要确认。")
        if payload.resolution == "merge" and not str(payload.merged_content or "").strip():
            raise HTTPException(status_code=422, detail="合并时必须提供最终记忆内容。")
        result = get_agent_service().resolve_memory_conflict(
            conflict_id,
            payload.resolution,
            merged_content=payload.merged_content,
        )
        if result.get("status") == "missing":
            raise HTTPException(status_code=404, detail="没有找到待处理冲突。")
        if result.get("status") in {"invalid", "error"}:
            raise HTTPException(status_code=422, detail="冲突处理没有完成。")
        return result

    @application.get(
        "/v1/memory-audit",
        dependencies=[Depends(require_local_access)],
    )
    def list_memory_audit(limit: int = Query(default=100, ge=1, le=500)):
        return get_agent_service().memory_audit_view(limit)

    @application.get(
        "/v1/conversations",
        response_model=List[ConversationSummarySchema],
        dependencies=[Depends(require_local_access)],
    )
    def list_conversations():
        return get_agent_service().list_web_sessions()

    @application.post(
        "/v1/conversations",
        response_model=ConversationSummarySchema,
        dependencies=[Depends(require_local_access)],
    )
    def create_conversation(payload: ConversationCreateSchema):
        return get_agent_service().create_web_session(payload.title)

    @application.get(
        "/v1/conversations/{conversation_id}",
        response_model=ConversationDetailSchema,
        dependencies=[Depends(require_local_access)],
    )
    def get_conversation(conversation_id: ConversationId):
        session = get_agent_service().get_web_session(conversation_id)
        if session is None:
            raise HTTPException(status_code=404, detail="没有找到这个 Web 会话。")
        return session

    @application.patch(
        "/v1/conversations/{conversation_id}",
        response_model=ConversationSummarySchema,
        dependencies=[Depends(require_local_access)],
    )
    def rename_conversation(
        conversation_id: ConversationId,
        payload: ConversationRenameSchema,
    ):
        session = get_agent_service().rename_web_session(
            conversation_id,
            payload.title,
        )
        if session is None:
            raise HTTPException(status_code=404, detail="没有找到这个 Web 会话。")
        return session

    return application


app = create_app()
