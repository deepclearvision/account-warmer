"""
Businesses router — CRUD for config/businesses.yaml + signal state reader.
"""

import json
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from api.deps import load_businesses, save_businesses, load_accounts, save_accounts, STATE_DIR

router = APIRouter(prefix="/api/businesses", tags=["businesses"])


class BusinessBody(BaseModel):
    name:        str
    type:        str = "plumber"
    address:     str
    share_link:  str = ""
    goal:        str = "review"   # review | edit
    area:        str = ""
    group:       str = ""         # logical grouping, e.g. "campaign_1"
    phone:       str = ""
    website:     str = ""
    location:    str = ""
    lat:         Optional[float] = None
    lng:         Optional[float] = None
    edit_field:  Optional[str] = None
    edit_value:  Optional[str] = None


class GroupAssignRequest(BaseModel):
    group:       str        # group name to assign from
    account_ids: list[str]  # accounts to distribute businesses across


class SetGroupRequest(BaseModel):
    ids:   list[str]  # business IDs to update
    group: str        # group name to set (empty string to clear)


# ── Routes (fixed-path routes MUST come before /{biz_id} parameterised ones) ──

@router.get("")
async def list_businesses():
    return await load_businesses()


@router.post("")
async def create_business(body: BusinessBody):
    businesses = await load_businesses()
    existing_ids = {b["id"] for b in businesses}
    n = 1
    while f"biz_{n:03d}" in existing_ids:
        n += 1
    new_biz = {"id": f"biz_{n:03d}", **body.model_dump()}
    businesses.append(new_biz)
    await save_businesses(businesses)
    return new_biz


@router.get("/groups")
async def list_groups():
    """Return all unique non-empty group names across businesses."""
    businesses = await load_businesses()
    groups = sorted({b.get("group", "") for b in businesses if b.get("group")})
    return groups


@router.post("/assign-group")
async def assign_group(body: GroupAssignRequest):
    """
    Distribute businesses in a group evenly across the given accounts.

    Each account receives roughly len(businesses) / len(accounts) businesses,
    spread by round-robin so the load is balanced. Existing target_businesses
    on each account are replaced with the new assignment.
    """
    if not body.group:
        raise HTTPException(400, "group must not be empty")
    if not body.account_ids:
        raise HTTPException(400, "account_ids must not be empty")

    businesses = await load_businesses()
    group_bizzes = [b for b in businesses if b.get("group") == body.group]
    if not group_bizzes:
        raise HTTPException(404, f"No businesses found in group {body.group!r}")

    accounts = await load_accounts()
    acc_map  = {a["id"]: a for a in accounts}
    sel_accs = [acc_map[aid] for aid in body.account_ids if aid in acc_map]
    if not sel_accs:
        raise HTTPException(404, "None of the supplied account_ids were found")

    n_accs = len(sel_accs)
    n_bizz = len(group_bizzes)

    # Round-robin: account[i] gets biz[i], biz[i + n_accs], biz[i + 2*n_accs], …
    for i, acc in enumerate(sel_accs):
        assigned = [group_bizzes[j]["id"] for j in range(i, n_bizz, n_accs)]
        acc["target_businesses"] = assigned

    await save_accounts(accounts)
    return {
        "group":      body.group,
        "businesses": n_bizz,
        "accounts":   n_accs,
        "distribution": {
            acc["id"]: [
                group_bizzes[j]["id"] for j in range(i, n_bizz, n_accs)
            ]
            for i, acc in enumerate(sel_accs)
        },
    }


@router.post("/set-group")
async def set_group_on_businesses(body: SetGroupRequest):
    """Set (or clear) the group field on a list of selected businesses."""
    if not body.ids:
        raise HTTPException(400, "ids must not be empty")
    businesses = await load_businesses()
    id_set = set(body.ids)
    count = 0
    for b in businesses:
        if b["id"] in id_set:
            if body.group:
                b["group"] = body.group
            else:
                b.pop("group", None)
            count += 1
    await save_businesses(businesses)
    return {"updated": count, "group": body.group}


@router.put("/{biz_id}")
async def update_business(biz_id: str, body: BusinessBody):
    businesses = await load_businesses()
    target = next((b for b in businesses if b["id"] == biz_id), None)
    if not target:
        raise HTTPException(404, f"Business {biz_id!r} not found")
    target.update(body.model_dump(exclude_none=False))
    await save_businesses(businesses)
    return target


@router.delete("/{biz_id}")
async def delete_business(biz_id: str):
    businesses = await load_businesses()
    new_list = [b for b in businesses if b["id"] != biz_id]
    if len(new_list) == len(businesses):
        raise HTTPException(404, f"Business {biz_id!r} not found")
    await save_businesses(new_list)
    return {"deleted": biz_id}


@router.get("/{biz_id}/signals")
async def get_business_signals(biz_id: str):
    """Return signal phase state for all accounts working towards this business."""
    results = []
    if not STATE_DIR.exists():
        return results
    for state_file in STATE_DIR.glob(f"*_biz_{biz_id}.json"):
        try:
            account_id = state_file.stem.split(f"_biz_{biz_id}")[0]
            with open(state_file, encoding="utf-8") as f:
                state = json.load(f)
            results.append({"account_id": account_id, **state})
        except Exception:
            continue
    return results
