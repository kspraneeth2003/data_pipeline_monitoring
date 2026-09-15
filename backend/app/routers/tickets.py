from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session, joinedload

from app import models, schemas
from app.db import get_db

router = APIRouter(prefix="/api/tickets", tags=["tickets"])


@router.get("", response_model=list[schemas.TicketWithContextOut])
def list_tickets(db: Session = Depends(get_db)):
    tickets = (
        db.query(models.Ticket)
        .options(
            joinedload(models.Ticket.check_run)
            .joinedload(models.CheckRun.check)
            .joinedload(models.Check.database)
            .joinedload(models.Database.project)
        )
        .order_by(models.Ticket.created_at.desc())
        .all()
    )
    return [
        schemas.TicketWithContextOut(
            **schemas.TicketOut.model_validate(t).model_dump(),
            check_run_id=t.check_run_id,
            check_id=t.check_run.check.id,
            check_name=t.check_run.check.name,
            database_slug=t.check_run.check.database.slug,
            database_name=t.check_run.check.database.name,
            project_slug=t.check_run.check.database.project.slug,
        )
        for t in tickets
    ]


@router.patch("/{ticket_id}", response_model=schemas.TicketOut)
def update_ticket(ticket_id: str, payload: schemas.TicketUpdate, db: Session = Depends(get_db)):
    ticket = db.query(models.Ticket).filter_by(id=ticket_id).first()
    if not ticket:
        raise HTTPException(404, "Ticket not found")
    if payload.status is not None:
        ticket.status = payload.status
    if payload.assignee is not None:
        ticket.assignee = payload.assignee
    db.commit()
    db.refresh(ticket)
    return ticket
