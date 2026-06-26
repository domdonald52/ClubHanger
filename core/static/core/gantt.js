(function () {
  "use strict";

  // ---- Auto-fit zoom -------------------------------------------------------
  // Redirect with ?zoom=X the first time so the server renders at the ideal
  // px/min for the current viewport. Nav links carry the zoom forward so this
  // redirect only fires when zoom is missing (e.g. first load or new device).
  (function autoZoom() {
    const params = new URLSearchParams(location.search);
    if (params.has("zoom")) return; // already set — no redirect needed
    const scroll = document.querySelector(".grid-scroll");
    if (!scroll) return;
    const cfg0 = JSON.parse(document.getElementById("cal-data").textContent);
    const available = scroll.clientWidth - (cfg0.serverLabelW || 160) - 2; // label + 2px borders
    if (available <= 0 || !cfg0.totalMins) return;
    const ideal = available / cfg0.totalMins;
    if (Math.abs(ideal - cfg0.pxPerMin) / cfg0.pxPerMin > 0.02) {
      params.set("zoom", ideal.toFixed(3));
      location.replace(location.pathname + "?" + params.toString());
    }
  })();

  const cfg = JSON.parse(document.getElementById("cal-data").textContent);
  const MEMBERS = JSON.parse(document.getElementById("members-data").textContent);
  const AIRCRAFT = JSON.parse(document.getElementById("aircraft-data").textContent);
  const INSTRUCTORS = JSON.parse(document.getElementById("instructors-data").textContent);
  const FLIGHT_TYPES = JSON.parse(document.getElementById("flight-types-data").textContent);
  const CONTACTS = JSON.parse((document.getElementById("contacts-data") || {textContent:"[]"}).textContent);
  const CONTACT_TYPES = JSON.parse((document.getElementById("contact-types-data") || {textContent:"[]"}).textContent);

  const PX = cfg.pxPerMin;
  const SLOT = cfg.slotMin;
  const dayStart = new Date(cfg.dayStart);

  // ---- date picker navigation -----------------------------------------
  const datePick = document.getElementById("cal-datepick");
  if (datePick) {
    datePick.addEventListener("change", () => {
      const v = datePick.value;
      if (!v) return;
      const [y, m, d] = v.split("-").map((n) => parseInt(n, 10));
      const zoom = new URLSearchParams(location.search).get("zoom");
      const qs = zoom ? `?zoom=${zoom}` : "";
      window.location.href = `/calendar/${cfg.clubSlug}/${y}/${m}/${d}/${qs}`;
    });
  }

  // ---- helpers ---------------------------------------------------------
  function snap(min) { return Math.round(min / SLOT) * SLOT; }
  function pxToMin(px) { return px / PX; }
  function minToPx(min) { return min * PX; }
  function startFromLeft(leftPx) {
    const mins = Math.floor(pxToMin(leftPx) / SLOT) * SLOT;
    return new Date(dayStart.getTime() + mins * 60000);
  }
  function fmtLocalInput(d) {
    const pad = (n) => String(n).padStart(2, "0");
    return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
  }
  function fmtHHMM(d) {
    const pad = (n) => String(n).padStart(2, "0");
    return `${pad(d.getHours())}:${pad(d.getMinutes())}`;
  }
  function toast(msg, type) {
    if (window.toast) { window.toast(msg, type || ''); return; }
    // fallback if window.toast not yet loaded
    const t = document.getElementById("toast");
    if (!t) return;
    t.textContent = msg; t.hidden = false;
    clearTimeout(t._timer);
    t._timer = setTimeout(() => (t.hidden = true), 3500);
  }
  // showToast is an alias used in various handlers
  const showToast = toast;

  function post(url, data) {
    const body = new URLSearchParams(data);
    return fetch(url, {
      method: "POST",
      headers: { "X-CSRFToken": cfg.csrf, "Content-Type": "application/x-www-form-urlencoded" },
      body,
    }).then(async (r) => {
      let j = {};
      try { j = await r.json(); } catch (e) {}
      return { ok: r.ok, status: r.status, data: j };
    });
  }

  // ---- overlap check (client-side, server is source of truth) ----------
  function overlapsInTrack(track, leftPx, widthPx, ignoreId) {
    const a0 = leftPx, a1 = leftPx + widthPx;
    let hit = false;
    track.querySelectorAll(".pill").forEach((p) => {
      if (p.dataset.id === String(ignoreId)) return;
      const b0 = parseFloat(p.style.left), b1 = b0 + parseFloat(p.style.width);
      if (a0 < b1 && b0 < a1) hit = true;
    });
    return hit;
  }

  // ---- modal -----------------------------------------------------------
  const modal = document.getElementById("modal");
  const fId = document.getElementById("m-booking-id");
  const fMember = document.getElementById("m-member");
  const fFlightType = document.getElementById("m-flight-type");
  const fAircraft = document.getElementById("m-aircraft");
  const fInstructor = document.getElementById("m-instructor");
  const fInstructorLabel = document.getElementById("m-instructor-label");
  const fStart = document.getElementById("m-start");
  const fDuration = document.getElementById("m-duration");
  const fDesc = document.getElementById("m-desc");
  const fClient = document.getElementById("m-client");
  const fBilledTo = document.getElementById("m-billed-to");
  const fBilledToLabel = document.getElementById("m-billed-to-label");
  const fClientLabel = document.getElementById("m-client-label");
  const fMemberDisplay = document.getElementById("m-member-display");
  const fFindMemberBtn = document.getElementById("m-find-member");
  const btnDelete = document.getElementById("m-delete");
  const editFields = document.getElementById("m-edit-fields");
  let _currentPill = null;

  // Mutable set — updated on watch toggles without page reload
  const watchedIds = new Set((cfg.watchedIds || []).map(String));

  // ---- new-booking preview pill --------------------------------------
  let previewEl = null;

  function updatePreview() {
    if (!previewEl) return;
    const acId = fAircraft.value;
    const track = document.querySelector(`.track[data-row-type="aircraft"][data-resource-id="${acId}"]`);
    if (!track) { previewEl.hidden = true; return; }
    if (previewEl.parentElement !== track) track.appendChild(previewEl);
    const startVal = fStart.value;
    const durVal = parseInt(fDuration.value) || 0;
    if (!startVal || durVal <= 0) { previewEl.hidden = true; return; }
    previewEl.hidden = false;
    const startDate = new Date(startVal);
    const leftPx = (startDate.getTime() - dayStart.getTime()) / 60000 * PX;
    previewEl.style.left = Math.max(0, leftPx) + "px";
    previewEl.style.width = (durVal * PX) + "px";
    const endDate = new Date(startDate.getTime() + durVal * 60000);
    previewEl.querySelector("span").textContent = `${fmtHHMM(startDate)}–${fmtHHMM(endDate)}`;
  }

  function removePreview() {
    if (previewEl) { previewEl.remove(); previewEl = null; }
  }

  function fillSelect(sel, items, valKey, labelKey, includeBlank) {
    sel.innerHTML = includeBlank ? '<option value="">— none —</option>' : "";
    items.forEach((it) => {
      const o = document.createElement("option");
      o.value = it[valKey];
      o.textContent = it[labelKey];
      sel.appendChild(o);
    });
  }
  // Populate member select with status badges
  function populateMembers(filter) {
    const prev = fMember.value;
    fMember.innerHTML = "";
    const q = (filter || '').toLowerCase();
    MEMBERS.forEach((m) => {
      if (q && !m.name.toLowerCase().includes(q)) return;
      const o = document.createElement("option");
      o.value = m.id;
      const badge = m.badge === 'current' ? '' : m.badge === 'non_member' ? ' ·Non-mbr' : ' ·Lapsed';
      const acct  = m.acct_warning ? ' ⚠' : '';
      o.textContent = m.name + badge + acct;
      if (m.badge !== 'current') o.style.color = '#adb5bd';
      fMember.appendChild(o);
    });
    // With no filter, restore the previous selection.
    // With a filter, let the first matching result show (don't restore — that's the whole point).
    if (!q && prev) fMember.value = prev;
  }
  populateMembers();
  fillSelect(fAircraft, AIRCRAFT, "id", "reg", false);
  // Show rostered + always-available (null) instructors; on_roster===false = off roster today
  fillSelect(fInstructor, INSTRUCTORS.filter(i => i.on_roster !== false), "id", "name", true);
  // Flight type dropdown: blank default; instructors see Contact/Member optgroups, others see member-only
  (function() {
    const blank = document.createElement("option"); blank.value = ""; fFlightType.appendChild(blank);
    const memberFTs  = FLIGHT_TYPES.filter(ft => !ft.for_contacts);
    const contactFTs = FLIGHT_TYPES.filter(ft =>  ft.for_contacts);
    const ftLabel = ft => ft.name + (ft.for_contacts ? '' : ft.is_solo ? ' · Solo' : ' · Instructor');
    if ((cfg.isInstructor || cfg.canManage) && contactFTs.length) {
      const mg = document.createElement("optgroup"); mg.label = "Member flights";
      memberFTs.forEach(ft => { const o = document.createElement("option"); o.value = ft.id; o.textContent = ftLabel(ft); mg.appendChild(o); });
      const cg = document.createElement("optgroup"); cg.label = "Contact flights";
      contactFTs.forEach(ft => { const o = document.createElement("option"); o.value = ft.id; o.textContent = ftLabel(ft); cg.appendChild(o); });
      fFlightType.appendChild(mg);
      fFlightType.appendChild(cg);
    } else {
      memberFTs.forEach(ft => { const o = document.createElement("option"); o.value = ft.id; o.textContent = ftLabel(ft); fFlightType.appendChild(o); });
    }
  })();

  const memberNotice = document.getElementById("m-member-notice");
  function updateMemberNotice() {
    const m = MEMBERS.find((x) => String(x.id) === fMember.value);
    if (!m) { memberNotice.style.display = 'none'; return; }
    const msgs = [];
    if (m.badge === 'lapsed')      msgs.push("Member is not current (lapsed or suspended).");
    if (m.badge === 'non_member')  msgs.push("Non-member record — confirm booking is appropriate.");
    if (m.acct_warning)            msgs.push("This member has a negative account balance.");
    if (msgs.length) {
      memberNotice.textContent = msgs.join(' ');
      memberNotice.style.display = 'block';
    } else {
      memberNotice.style.display = 'none';
    }
  }
  fMember.addEventListener("change", updateMemberNotice);

  function updateMemberDisplay() {
    if (!fMemberDisplay) return;
    const m = MEMBERS.find(x => String(x.id) === fMember.value);
    if (m) {
      fMemberDisplay.textContent = m.name;
      fMemberDisplay.style.color = m.badge !== "current" ? "var(--text-3)" : "var(--text-1)";
    } else {
      fMemberDisplay.textContent = "— select member —";
      fMemberDisplay.style.color = "var(--text-3)";
    }
  }

  // Hide member selector for non-staff — they always book for themselves
  if (!cfg.canManage) {
    const memberLabel = fMember.closest("label");
    if (memberLabel) memberLabel.hidden = true;
  }

  // Populate contacts dropdown (staff only — fClient is null for non-staff)
  if (fClient && CONTACTS.length) {
    CONTACTS.forEach((c) => {
      const o = document.createElement("option");
      o.value = c.id;
      o.textContent = c.name + (c.type ? ` (${c.type})` : "");
      fClient.appendChild(o);
    });
  }
  const memberLbl = document.getElementById("m-member-lbl");
  // When client is chosen show billed-to; everything else driven by syncFlightTypeMode
  if (fClient) fClient.addEventListener("change", () => {
    if (fBilledToLabel) fBilledToLabel.style.display = fClient.value ? "" : "none";
  });
  function updateClientState() {
    if (fBilledToLabel) fBilledToLabel.style.display = (fClient && fClient.value) ? "" : "none";
  }

  // ---- Quick-add contact mini-modal ------------------------------------
  const quickContactModal = document.getElementById("quick-contact-modal");
  if (quickContactModal) {
    const qcName  = document.getElementById("qc-name");
    const qcType  = document.getElementById("qc-type");
    const qcEmail = document.getElementById("qc-email");
    const qcPhone = document.getElementById("qc-phone");
    const qcError = document.getElementById("qc-error");

    // Populate contact type dropdown once
    CONTACT_TYPES.forEach((ct) => {
      const o = document.createElement("option");
      o.value = ct.id; o.textContent = ct.name;
      qcType.appendChild(o);
    });

    function openQuickContact() {
      qcName.value = ""; qcType.value = ""; qcEmail.value = ""; qcPhone.value = "";
      qcError.hidden = true;
      quickContactModal.hidden = false;
      qcName.focus();
    }
    function closeQuickContact() { quickContactModal.hidden = true; }

    const btnClientNew = document.getElementById("m-client-new");
    if (btnClientNew) btnClientNew.addEventListener("click", openQuickContact);
    document.getElementById("qc-close").addEventListener("click", closeQuickContact);
    document.getElementById("qc-cancel").addEventListener("click", closeQuickContact);
    quickContactModal.addEventListener("click", (e) => { if (e.target === quickContactModal) closeQuickContact(); });

    document.getElementById("qc-save").addEventListener("click", () => {
      const name = qcName.value.trim();
      if (!name) { qcError.textContent = "Name is required."; qcError.hidden = false; return; }
      qcError.hidden = true;
      post(cfg.contactCreateUrl, {
        name,
        contact_type: qcType.value,
        email: qcEmail.value.trim(),
        phone: qcPhone.value.trim(),
      }).then((res) => {
        if (res.ok && res.data.success) {
          const c = { id: res.data.id, name: res.data.name, type: res.data.type };
          CONTACTS.push(c);
          CONTACTS.sort((a, b) => a.name.localeCompare(b.name));
          if (fClient) {
            const o = document.createElement("option");
            o.value = c.id;
            o.textContent = c.name + (c.type ? ` (${c.type})` : "");
            // Insert in alphabetical order after the blank option
            let inserted = false;
            for (const existing of Array.from(fClient.options)) {
              if (existing.value && c.name.localeCompare(existing.textContent.split(" (")[0]) <= 0) {
                fClient.insertBefore(o, existing);
                inserted = true;
                break;
              }
            }
            if (!inserted) fClient.appendChild(o);
            fClient.value = c.id;
            updateClientState();
          }
          closeQuickContact();
        } else {
          qcError.textContent = res.data.error || "Could not create contact.";
          qcError.hidden = false;
        }
      });
    });
  }

  // ---- Find member modal -----------------------------------------------
  const findMemberModal = document.getElementById("find-member-modal");
  const fmSearch  = document.getElementById("fm-search");
  const fmList    = document.getElementById("fm-list");
  const fmApply   = document.getElementById("fm-apply");
  let   _fmSelected = null; // { id, el } of highlighted row

  function _fmHighlight(item, memberId) {
    if (_fmSelected) _fmSelected.el.classList.remove("selected");
    _fmSelected = { id: memberId, el: item };
    item.classList.add("selected");
    if (fmApply) { fmApply.disabled = false; }
  }

  function renderFmList(filter) {
    if (!fmList) return;
    const q = (filter || "").toLowerCase();
    fmList.innerHTML = "";
    _fmSelected = null;
    if (fmApply) fmApply.disabled = true;
    let count = 0;
    MEMBERS.forEach(m => {
      if (q && !m.name.toLowerCase().includes(q)) return;
      const badge = m.badge === "current" ? "" : m.badge === "non_member" ? " · Non-mbr" : " · Lapsed";
      const warn  = m.acct_warning ? " ⚠" : "";
      const item  = document.createElement("div");
      item.className = "fm-item" + (m.badge !== "current" ? " inactive" : "");
      item.textContent = m.name + badge + warn;
      item.dataset.memberId = m.id;
      item.addEventListener("click", () => _fmHighlight(item, m.id));
      item.addEventListener("dblclick", () => { _fmHighlight(item, m.id); _fmConfirm(); });
      fmList.appendChild(item);
      count++;
    });
    if (!count) {
      fmList.innerHTML = '<div class="fm-empty">No members found</div>';
    }
  }

  function _fmConfirm() {
    if (!_fmSelected) return;
    fMember.value = _fmSelected.id;
    updateMemberDisplay();
    updateMemberNotice();
    if (findMemberModal) findMemberModal.hidden = true;
  }

  if (findMemberModal) {
    function _openFindMember() {
      _fmSelected = null;
      if (fmApply) fmApply.disabled = true;
      if (fmSearch) fmSearch.value = "";
      renderFmList("");
      findMemberModal.hidden = false;
      setTimeout(() => fmSearch && fmSearch.focus(), 50);
    }
    if (fMemberDisplay) fMemberDisplay.addEventListener("click", _openFindMember);
    if (fFindMemberBtn) fFindMemberBtn.addEventListener("click", _openFindMember);
    if (fmSearch) {
      fmSearch.addEventListener("input", () => renderFmList(fmSearch.value));
      fmSearch.addEventListener("keydown", e => {
        if (e.key === "Enter") {
          if (_fmSelected) { _fmConfirm(); return; }
          const first = fmList && fmList.querySelector("[data-member-id]");
          if (first) { first.click(); }
        }
      });
    }
    if (fmApply)  fmApply.addEventListener("click", _fmConfirm);
    document.getElementById("fm-close").addEventListener("click", () => { findMemberModal.hidden = true; });
    document.getElementById("fm-cancel").addEventListener("click", () => { findMemberModal.hidden = true; });
    findMemberModal.addEventListener("click", e => { if (e.target === findMemberModal) findMemberModal.hidden = true; });
  }

  // Single function driven by flight type selection — controls all dependent fields
  function syncFlightTypeMode() {
    const ft         = FLIGHT_TYPES.find(x => String(x.id) === fFlightType.value);
    const isContact  = ft ? ft.for_contacts : false;
    const isSolo     = ft ? ft.is_solo      : false;
    const hasType    = !!ft;

    // Member field label
    if (memberLbl) memberLbl.textContent = isContact ? "Pilot / instructor" : "Member";

    // Instructor field: show only for dual member flights
    if (fInstructorLabel) {
      const showInstr = hasType && !isSolo && !isContact;
      fInstructorLabel.hidden = !showInstr;
      if (hasType && !showInstr) fInstructor.value = "";
    }

    // Contact row: show only for contact flight types
    const clientLabel = document.getElementById("m-client-label");
    if (clientLabel) clientLabel.hidden = !isContact;

    // Billed-to: show when contact type AND a client is chosen
    if (fBilledToLabel) {
      fBilledToLabel.style.display = (isContact && fClient && fClient.value) ? "" : "none";
    }

    // Switching TO contact mode: auto-fill member with current user if blank
    if (isContact && fMember && !fMember.value) {
      fMember.value = String(cfg.currentUserId);
      updateMemberNotice();
      updateMemberDisplay();
    }

    // Switching AWAY from contact mode: clear client fields
    if (!isContact) {
      if (fClient)        fClient.value = "";
      if (fBilledTo)      fBilledTo.value = "";
    }
  }
  fFlightType.addEventListener("change", syncFlightTypeMode);

  function openCreate(aircraftId, start, instructorId, bookingKind) {
    if (start < new Date()) {
      toast("Bookings cannot be made in the past");
      return;
    }
    document.getElementById("modal-title").textContent = "New booking";
    fId.value = "";
    btnDelete.hidden = true;
    if (aircraftId) fAircraft.value = aircraftId;
    // Restore full instructor list (may have been filtered by a previous find-slot open)
    fillSelect(fInstructor, INSTRUCTORS.filter(function(i){ return i.on_roster !== false; }), "id", "name", true);
    fInstructor.value = instructorId || "";
    fStart.value = fmtLocalInput(start);
    fDuration.value = cfg.defaultDuration;
    fDesc.value = "";
    fFlightType.value = "";
    if (!cfg.canManage) {
      fMember.value = cfg.currentUserId;
    } else {
      fMember.value = "";
    }
    updateMemberNotice();
    updateMemberDisplay();
    syncFlightTypeMode();
    conflictNotice.hidden = true;
    conflictNotice.innerHTML = "";
    if (instrConflictNotice) instrConflictNotice.hidden = true;
    modal.hidden = false;
    removePreview();
    previewEl = document.createElement("div");
    previewEl.className = "preview";
    previewEl.appendChild(document.createElement("span"));
    const acTrack = document.querySelector(`.track[data-row-type="aircraft"][data-resource-id="${fAircraft.value}"]`);
    if (acTrack) acTrack.appendChild(previewEl);
    updatePreview();
  }
  const conflictNotice = document.getElementById("m-conflict-notice");

  function buildConflictNotice(pill) {
    const items = [];
    const types = (pill.dataset.issueTypes || "").split(",").filter(Boolean);
    const reason = pill.dataset.conflictReason || "";

    if (types.includes("blockout")) {
      const boRaw = reason.split(";").find(r => r.includes("Overlaps") || r.includes("block-out")) || reason;
      const boName = boRaw.replace(/^Overlaps\s+/i, "").trim() || boRaw;
      items.push({
        icon: "⊘",
        title: "Block-out",
        detail: boName,
        tip: "",
      });
    }
    if (types.includes("member")) {
      const standing = pill.dataset.memberStanding || "not current";
      items.push({
        icon: "⚑",
        title: `Member ${standing}`,
        detail: `${pill.dataset.member}'s membership is not current.`,
        tip: "Update their membership status in Manage → Members, or cancel and reassign this booking.",
      });
    }
    if (types.includes("aircraft")) {
      items.push({
        icon: "✈",
        title: "Aircraft retired",
        detail: `${pill.dataset.aircraftId ? "This aircraft" : "The assigned aircraft"} has been marked as retired.`,
        tip: "Move this booking to an active aircraft or cancel it.",
      });
    }

    if (types.includes("instructor_roster")) {
      items.push({
        icon: "👤",
        title: "Instructor off roster",
        detail: "The assigned instructor is not rostered for this date.",
        tip: "Reassign to a rostered instructor or update their availability schedule.",
      });
    }

    // Ghost track = instructor role changed or inactive (catches cases not in issue_types)
    const track = pill.parentElement;
    if (track && track.dataset.ghost === "true" && !types.includes("instructor_roster")) {
      const gr = track.dataset.ghostReason;
      const label = gr === "role_changed" ? "Instructor role removed"
                  : gr === "inactive"     ? "Instructor inactive"
                  : gr === "off_roster"   ? "Instructor off roster today"
                  : "Resource unavailable";
      items.push({
        icon: "👤",
        title: label,
        detail: "The assigned instructor is no longer available for this booking.",
        tip: "Reassign to another instructor or cancel.",
      });
    }

    if (!items.length) return null;
    // Compact badge row — one pill per issue
    const badges = items.map(it => {
      const label = it.detail ? `${it.title}: ${it.detail}` : it.title;
      return `<span style="display:inline-flex;align-items:center;gap:.25rem;font-size:.76rem;font-weight:600;padding:.2rem .55rem;border-radius:10px;background:#fff0f0;color:#c0392b;border:1px solid #fca5a5;white-space:nowrap;">${it.icon} ${label}</span>`;
    }).join('');
    return `<div style="padding:.35rem .55rem;display:flex;flex-wrap:wrap;gap:.3rem;">${badges}</div>`;
  }

  function openEdit(pill) {
    removePreview();
    _currentPill = pill;
    document.getElementById("modal-title").textContent = "Edit booking";
    fId.value = pill.dataset.id;
    btnDelete.hidden = false;
    btnSave.hidden = false;
    fAircraft.value = pill.dataset.aircraftId || "";
    fInstructor.value = pill.dataset.instructorId || "";
    // If the booking's instructor is off-roster today, they won't be in the list — add them
    if (pill.dataset.instructorId && fInstructor.value !== pill.dataset.instructorId) {
      const instr = INSTRUCTORS.find(i => String(i.id) === pill.dataset.instructorId);
      if (instr) {
        const o = document.createElement("option");
        o.value = String(instr.id);
        o.textContent = instr.name + " (off roster)";
        fInstructor.appendChild(o);
        fInstructor.value = String(instr.id);
      }
    }
    fStart.value = fmtLocalInput(new Date(pill.dataset.start));
    fDuration.value = pill.dataset.duration;
    fDesc.value = pill.dataset.desc || "";
    fFlightType.value = pill.dataset.flightTypeId || "";
    const m = MEMBERS.find((x) => x.name === pill.dataset.member);
    if (m) fMember.value = m.id;
    updateMemberNotice();
    updateMemberDisplay();
    if (fClient) fClient.value = pill.dataset.clientId || "";
    if (fBilledTo) fBilledTo.value = pill.dataset.billedTo || "";
    syncFlightTypeMode();

    const noticeHtml = buildConflictNotice(pill);
    if (noticeHtml) {
      conflictNotice.innerHTML = noticeHtml;
      conflictNotice.hidden = false;
    } else {
      conflictNotice.hidden = true;
      conflictNotice.innerHTML = "";
    }

    // Plain members editing their own booking: lock member + instructor fields
    const _memberOwned = !cfg.canManage && (pill.dataset.memberUserId === String(cfg.currentUserId));
    fMember.disabled = _memberOwned;
    fMember.style.opacity = _memberOwned ? ".5" : "";
    if (fFindMemberBtn) { fFindMemberBtn.disabled = _memberOwned; fFindMemberBtn.style.opacity = _memberOwned ? ".5" : ""; }
    if (fMemberDisplay) { fMemberDisplay.style.opacity = _memberOwned ? ".5" : ""; fMemberDisplay.style.cursor = _memberOwned ? "default" : "pointer"; }
    fInstructor.disabled = _memberOwned;
    if (fInstructorLabel) fInstructorLabel.style.opacity = _memberOwned ? ".5" : "";

    modal.hidden = false;
  }
  const btnSave = document.getElementById("m-save");

  function closeModal() {
    modal.hidden = true;
    removePreview();
    fMember.disabled = false; fMember.style.opacity = "";
    if (fFindMemberBtn) { fFindMemberBtn.disabled = false; fFindMemberBtn.style.opacity = ""; }
    if (fMemberDisplay) { fMemberDisplay.style.opacity = ""; fMemberDisplay.style.cursor = "pointer"; }
    fInstructor.disabled = false;
    if (fInstructorLabel) fInstructorLabel.style.opacity = "";
    if (fClient) fClient.value = "";
    if (fBilledTo) fBilledTo.value = "";
    if (fBilledToLabel) fBilledToLabel.style.display = "none";
    updateClientState();
    editFields.style.display = "";
    btnSave.hidden = false;
    btnDelete.hidden = false;
  }
  document.getElementById("m-cancel").addEventListener("click", closeModal);
  modal.addEventListener("click", (e) => { if (e.target === modal) closeModal(); });

  fStart.addEventListener("input", updatePreview);
  fDuration.addEventListener("input", updatePreview);
  fAircraft.addEventListener("change", updatePreview);

  // Instructor availability — warn if the selected instructor already has a booking at this time
  const instrConflictNotice = document.getElementById("m-instructor-conflict");
  function checkInstructorConflict() {
    if (!instrConflictNotice) return;
    const instrId = fInstructor.value;
    if (!instrId || !fStart.value || !fDuration.value) { instrConflictNotice.hidden = true; return; }
    const instrTrack = document.querySelector(`.track[data-row-type="instructor"][data-resource-id="${instrId}"]`);
    if (!instrTrack) { instrConflictNotice.hidden = true; return; }
    const startDate = new Date(fStart.value);
    const leftPx = (startDate.getTime() - dayStart.getTime()) / 60000 * PX;
    const widthPx = (parseInt(fDuration.value) || 0) * PX;
    const editId = fId.value || null;
    if (overlapsInTrack(instrTrack, leftPx, widthPx, editId)) {
      instrConflictNotice.hidden = false;
    } else {
      instrConflictNotice.hidden = true;
    }
  }
  fInstructor.addEventListener("change", checkInstructorConflict);
  fStart.addEventListener("input", checkInstructorConflict);
  fDuration.addEventListener("input", checkInstructorConflict);

  function isOutsideTypical(startDate, durationMin) {
    const typStart = cfg.typicalStart.split(":").map(Number);
    const typEnd = cfg.typicalEnd.split(":").map(Number);
    const typStartMins = typStart[0] * 60 + typStart[1];
    const typEndMins = typEnd[0] * 60 + typEnd[1];
    const startMins = startDate.getHours() * 60 + startDate.getMinutes();
    const endMins = startMins + (parseInt(durationMin) || 0);
    return startMins < typStartMins || endMins > typEndMins;
  }

  document.getElementById("m-save").addEventListener("click", () => {
    const id = fId.value;
    const startDate = new Date(fStart.value);
    const payload = {
      member_id: fMember.value,
      aircraft_id: fAircraft.value,
      instructor_id: fInstructor.value,
      flight_type_id: fFlightType.value,
      start_time: startDate.toISOString(),
      duration: fDuration.value,
      description: fDesc.value,
      client_id: fClient ? (fClient.value || "") : "",
      billed_to: fBilledTo ? (fBilledTo.value || "") : "",
      club_slug: cfg.clubSlug,
    };
    const url = id ? `/api/booking/${id}/edit/` : cfg.createUrl;
    const doSave = () => {
      const submit = (withOverride) => {
        const p = withOverride ? Object.assign({}, payload, { override: "1" }) : payload;
        post(url, p).then((res) => {
          if (res.ok && res.data.success) { location.reload(); return; }
          if (res.status === 409 && res.data.blockout && res.data.can_override) {
            const confirmMsg = res.data.soft
              ? res.data.error  // soft: advisory message already has a call-to-action
              : res.data.error + "\n\nBook over this block-out anyway?";
            askConfirm(confirmMsg, () => submit(true), () => {});
            return;
          }
          toast(res.data.error || "Could not save booking", 'err');
        });
      };
      submit(false);
    };
    if (isOutsideTypical(startDate, fDuration.value)) {
      const typMsg = `This booking falls outside typical hours (${cfg.typicalStart}–${cfg.typicalEnd}).\n\nConfirm you intend to book off-hours?`;
      askConfirm(typMsg, doSave, () => {});
    } else {
      doSave();
    }
  });

  const cancelChoiceModal = document.getElementById("cancel-choice");
  const cancelReleaseCheck = document.getElementById("cancel-release-check");
  const cancelReasonSel    = document.getElementById("cancel-reason");
  const cancelReasonOtherW = document.getElementById("cancel-reason-other-wrap");
  const cancelReasonOther  = document.getElementById("cancel-reason-other");
  const ccKeep = document.getElementById("cc-keep");
  const ccConfirm = document.getElementById("cc-confirm");

  cancelReasonSel.addEventListener("change", () => {
    cancelReasonOtherW.hidden = cancelReasonSel.value !== "other";
    if (cancelReasonSel.value !== "other") cancelReasonOther.value = "";
  });

  btnDelete.addEventListener("click", () => {
    if (!fId.value) return;
    cancelReleaseCheck.checked = false;
    cancelReasonSel.value = "";
    cancelReasonOtherW.hidden = true;
    cancelReasonOther.value = "";
    cancelChoiceModal.hidden = false;
  });
  ccKeep.addEventListener("click", () => { cancelChoiceModal.hidden = true; });
  ccConfirm.addEventListener("click", () => {
    if (!cancelReasonSel.value) {
      cancelReasonSel.style.borderColor = "#c0392b";
      setTimeout(() => cancelReasonSel.style.borderColor = "", 1500);
      return;
    }
    const id = fId.value;
    const release = cancelReleaseCheck.checked ? "1" : "0";
    const reason = cancelReasonSel.value;
    const reason_other = cancelReasonOther.value.trim();
    cancelChoiceModal.hidden = true;
    post(`/api/booking/${id}/reject/`, { release, reason, reason_other }).then((res) => {
      if (res.ok && res.data.success) location.reload();
      else toast(res.data.error || "Could not cancel", 'err');
    });
  });

  // ---- resource-change confirm ----------------------------------------
  const confirmBox = document.getElementById("confirm");
  let confirmCb = null;
  let confirmCancelCb = null;
  function askConfirm(msg, cb, onCancel) {
    document.getElementById("confirm-msg").textContent = msg;
    confirmCb = cb;
    confirmCancelCb = onCancel || null;
    confirmBox.hidden = false;
  }
  document.getElementById("c-cancel").addEventListener("click", () => {
    confirmBox.hidden = true;
    const cc = confirmCancelCb;
    confirmCb = null; confirmCancelCb = null;
    if (cc) cc(); else location.reload();
  });
  document.getElementById("c-ok").addEventListener("click", () => {
    confirmBox.hidden = true;
    const cb = confirmCb;
    confirmCb = null; confirmCancelCb = null;
    if (cb) cb();
  });

  // ---- click empty track to book --------------------------------------
  function hardBandAtX(track, xPx) {
    for (const band of track.querySelectorAll('.band[data-hard="true"]')) {
      const left = parseFloat(band.style.left);
      const width = parseFloat(band.style.width);
      if (xPx >= left && xPx <= left + width) return band;
    }
    return null;
  }

  if (cfg.canBook) {
    document.querySelectorAll('.track[data-row-type="aircraft"]:not([data-ghost])').forEach((track) => {
      track.classList.add("bookable");
      track.addEventListener("click", (e) => {
        if (_recentDrag || e.target.closest(".pill")) return;
        const rect = track.getBoundingClientRect();
        const x = (e.clientX - rect.left) / (window._ganttScale || 1);
        const hardBand = hardBandAtX(track, x);
        if (hardBand) {
          if (!cfg.canManage) {
            toast(`Blocked: ${hardBand.title || "this time is unavailable"}`);
            return;
          }
          // Staff: confirm before opening — makes override intent explicit
          askConfirm(
            `Hard block-out in effect: ${hardBand.title || "this time is blocked"}.\n\nOpen as a staff override?`,
            () => openCreate(track.dataset.resourceId, startFromLeft(x)),
            () => {}
          );
          return;
        }
        openCreate(track.dataset.resourceId, startFromLeft(x));
      });
    });
  }

  // deep-link handled below, after window.openNewBookingWith is defined

  // ---- watch modal (non-staff viewing another member's booking) --------
  const watchModal = document.getElementById("watch-modal");
  const wmClose = document.getElementById("wm-close");
  const wmWatch = document.getElementById("wm-watch");
  const wmWatchLabel = document.getElementById("wm-watch-label");
  let wmBookingId = null;

  function openWatchModal(pill) {
    wmBookingId = pill.dataset.id;
    document.getElementById("wm-title").textContent = pill.dataset.member;
    document.getElementById("wm-detail").textContent = pill.title.split("·").slice(1).join("·").trim();
    const watching = watchedIds.has(wmBookingId);
    wmWatchLabel.textContent = watching ? "Unwatch" : "Watch slot";
    wmWatch.style.background = watching ? "#e53e3e" : "";
    wmWatch.style.borderColor = watching ? "#e53e3e" : "";
    watchModal.hidden = false;
  }
  wmClose.addEventListener("click", () => { watchModal.hidden = true; });
  wmWatch.addEventListener("click", () => {
    post(`/api/booking/${wmBookingId}/watch/`, {}).then((res) => {
      if (!res.ok) { toast(res.data.error || "Could not update watch"); return; }
      const watching = res.data.watching;
      if (watching) watchedIds.add(wmBookingId); else watchedIds.delete(wmBookingId);
      wmWatchLabel.textContent = watching ? "Unwatch" : "Watch slot";
      wmWatch.style.background = watching ? "#e53e3e" : "";
      wmWatch.style.borderColor = watching ? "#e53e3e" : "";
      toast(watching ? "Watching this slot" : "No longer watching");
    });
  });

  // ---- pill open-edit: dblclick for draggable pills, click for completed ----
  // Draggable pills use dblclick so an aborted drag doesn't accidentally open
  // the edit dialog. Completed pills can't be dragged so single-click is fine.
  document.querySelectorAll(".pill").forEach((pill) => {
    const evtName = cfg.canManage ? "dblclick" : "click";
    pill.addEventListener(evtName, (e) => {
      if (pill._didDrag) { pill._didDrag = false; return; }
      e.stopPropagation();
      if (cfg.canManage) {
        // All existing bookings go to the booking detail overlay — one path for the status cycle
        const detailUrl = cfg.bookingDetailBase.replace("/0/", "/" + pill.dataset.id + "/");
        if (window.openPageOverlay) window.openPageOverlay(detailUrl);
        else window.location.href = detailUrl;
      } else if (cfg.canBook) {
        const isOwn = pill.dataset.memberUserId === String(cfg.currentUserId);
        const st    = pill.dataset.status;
        const isActive = st !== "cancelled" && st !== "completed";
        if (isOwn && st === "pending") {
          openEdit(pill);
        } else if (isOwn && st === "confirmed") {
          const phone = cfg.clubPhone;
          const msg = phone
            ? `This booking is confirmed. To make changes please contact the club on ${phone}.`
            : "This booking is confirmed. To make changes please contact the club.";
          toast(msg);
        } else if (!isOwn && isActive) {
          openWatchModal(pill);
        }
      }
    });
  });

  // ---- drag + resize ---------------------------------------------------
  // Set for one animation frame after any drag so the track click-to-create
  // handler doesn't fire when the cursor lands on an empty area after a drop.
  let _recentDrag = false;

  // Escape cancels an in-progress drag.
  let _cancelActiveDrag = null;
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && _cancelActiveDrag) { _cancelActiveDrag(); }
  });

  // Cache all tracks for Y→row lookup. Aircraft ghosts excluded; instructor
  // ghosts included so admins can explicitly assign an off-roster instructor.
  const ALL_TRACKS = Array.from(document.querySelectorAll(
    ".track[data-row-type='aircraft']:not([data-ghost]), .track[data-row-type='instructor']"
  ));

  function trackAtY(clientY, rowType) {
    const tracks = rowType
      ? ALL_TRACKS.filter((t) => t.dataset.rowType === rowType)
      : ALL_TRACKS;
    let nearest = null, nearestDist = Infinity;
    for (const t of tracks) {
      const r = t.getBoundingClientRect();
      if (clientY >= r.top && clientY <= r.bottom) return t;
      const dist = Math.min(Math.abs(clientY - r.top), Math.abs(clientY - r.bottom));
      if (dist < nearestDist) { nearestDist = dist; nearest = t; }
    }
    return nearest;
  }

  if (cfg.canManage) {
    // Completed and departed pills are not draggable — skip makeInteractive so click fires naturally
    document.querySelectorAll(".pill").forEach((pill) => {
      if (pill.dataset.status !== "completed" && pill.dataset.status !== "departed") makeInteractive(pill);
    });
  }

  function makeInteractive(pill) {
    let mode = null; // 'move' | 'resize'
    let startX, startY, origLeft, origWidth, moved;
    let origRowType, origParent;
    let cursorOffsetX, cursorOffsetY; // where in the pill the pointer went down
    let capturedPointerId = null;
    let linkedPill = null;
    let dragGhost = null, linkedGhost = null;
    let linkedGhostTop = 0; // Y is fixed for the linked ghost — it never changes rows

    function _makeGhost(source, rect) {
      const g = source.cloneNode(true);
      Object.assign(g.style, {
        position: "fixed",
        left: rect.left + "px",
        top: rect.top + "px",
        width: rect.width + "px",
        height: rect.height + "px",
        margin: "0",
        zIndex: "9999",
        pointerEvents: "none",
        opacity: "0.92",
        boxShadow: "0 8px 24px rgba(16,24,40,.35)",
        cursor: "grabbing",
        transform: "none",
      });
      document.body.appendChild(g);
      return g;
    }

    function _clearDragState() {
      if (dragGhost)   { dragGhost.remove();   dragGhost   = null; }
      if (linkedGhost) { linkedGhost.remove();  linkedGhost = null; }
      pill.style.opacity = "";
      if (linkedPill)  { linkedPill.style.opacity = ""; linkedPill = null; }
      ALL_TRACKS.forEach((t) => t.classList.remove("drag-over"));
    }

    function cancelDrag() {
      _clearDragState();
      pill.style.width = origWidth + "px"; // restore if resize was in progress
      pill.classList.remove("drag", "invalid");
      try { if (capturedPointerId !== null) pill.releasePointerCapture(capturedPointerId); } catch(_) {}
      mode = null; moved = false; capturedPointerId = null;
      _cancelActiveDrag = null;
    }

    pill.addEventListener("pointerdown", (e) => {
      if (e.button !== 0) return;
      mode = e.target.hasAttribute("data-resize") ? "resize" : "move";
      if (mode === "move" && pill.dataset.status === "departed") {
        mode = null; // pill became departed in-page without reload — let dblclick open check-in normally
        return;
      }
      if (mode === "move" && pill.dataset.status === "completed") {
        mode = null; // reset so pointerup won't set _didDrag and block the click
        return;
      }
      const rect = pill.getBoundingClientRect();
      cursorOffsetX = e.clientX - rect.left;
      cursorOffsetY = e.clientY - rect.top;
      startX = e.clientX;
      startY = e.clientY;
      origLeft  = parseFloat(pill.style.left);
      origWidth = parseFloat(pill.style.width);
      moved = false;
      origRowType = pill.parentElement.dataset.rowType;
      origParent  = pill.parentElement;
      capturedPointerId = e.pointerId;
      linkedPill = null; dragGhost = null; linkedGhost = null;
      pill.setPointerCapture(e.pointerId);
      _cancelActiveDrag = cancelDrag;
      // No e.preventDefault() here — that would suppress native dblclick.
      // Drag is handled via pointer capture; scroll/select prevention is in pointermove.
    });

    pill.addEventListener("pointermove", (e) => {
      if (!mode) return;
      if (!moved && (Math.abs(e.clientX - startX) > 3 || Math.abs(e.clientY - startY) > 3)) moved = true;
      if (!moved) return;
      e.preventDefault(); // prevent scroll/text-select while actually dragging

      if (mode === "resize") {
        const dx = e.clientX - startX;
        pill.style.width = Math.max(minToPx(SLOT), origWidth + dx / (window._ganttScale || 1)) + "px";
        pill.classList.toggle("invalid", overlapsInTrack(pill.parentElement, origLeft, parseFloat(pill.style.width), pill.dataset.id));
        return;
      }

      // move — spawn ghosts on first significant movement
      if (!dragGhost) {
        const rect = pill.getBoundingClientRect();
        dragGhost = _makeGhost(pill, rect);
        pill.style.opacity = "0.25";
        pill.classList.add("drag");

        const otherType = origRowType === "aircraft" ? "instructor" : "aircraft";
        linkedPill = document.querySelector(
          `.track[data-row-type="${otherType}"] .pill[data-id="${pill.dataset.id}"]`
        ) || null;
        if (linkedPill) {
          const lRect = linkedPill.getBoundingClientRect();
          linkedGhostTop = lRect.top; // locked — never changes
          linkedGhost = _makeGhost(linkedPill, lRect);
          linkedGhost.style.zIndex = "9998";
          linkedPill.style.opacity = "0.25";
        }
      }

      // Ghost tracks cursor exactly — cursor-relative, no accumulated drift
      dragGhost.style.left = (e.clientX - cursorOffsetX) + "px";
      dragGhost.style.top  = (e.clientY - cursorOffsetY) + "px";

      // Linked ghost mirrors X only — stays locked to its row's Y
      if (linkedGhost) {
        linkedGhost.style.left = (e.clientX - cursorOffsetX) + "px";
        linkedGhost.style.top  = linkedGhostTop + "px";
      }

      const hoverTrack = trackAtY(e.clientY, origRowType);
      ALL_TRACKS.forEach((t) => t.classList.toggle("drag-over", t === hoverTrack));
    });

    pill.addEventListener("pointerup", (e) => {
      if (!mode) return;
      try { pill.releasePointerCapture(e.pointerId); } catch(_) {}
      capturedPointerId = null;
      _cancelActiveDrag = null;
      pill.classList.remove("drag");
      const finishedMode = mode;
      mode = null;

      if (!moved) {
        _clearDragState();
        pill._didDrag = false;
        return;
      }

      pill._didDrag = true;
      _recentDrag = true;
      requestAnimationFrame(() => { _recentDrag = false; });

      let newTrack = origParent;

      if (finishedMode === "move") {
        const target = trackAtY(e.clientY, origRowType) || origParent;
        // Ghost screen-left = e.clientX - cursorOffsetX; subtract track's screen-left for track-relative px
        // Divide by scale since track content is scaleX'd — screen px → natural px
        const rawLeft = (e.clientX - cursorOffsetX) - target.getBoundingClientRect().left;
        _clearDragState();
        if (target !== origParent) target.appendChild(pill);
        pill.style.left = Math.max(0, rawLeft / (window._ganttScale || 1)) + "px";
        newTrack = target;
      } else {
        _clearDragState();
      }

      const leftPx  = snap(pxToMin(parseFloat(pill.style.left)))  * PX;
      const widthPx = snap(pxToMin(parseFloat(pill.style.width))) * PX;
      pill.style.left  = leftPx  + "px";
      pill.style.width = widthPx + "px";

      if (overlapsInTrack(newTrack, leftPx, widthPx, pill.dataset.id)) {
        toast("That slot is already taken.");
        return location.reload();
      }

      const newStart      = startFromLeft(leftPx);
      const newDuration   = snap(pxToMin(widthPx));
      const newRowType    = newTrack.dataset.rowType;
      const newResourceId = String(newTrack.dataset.resourceId);
      const payload       = { new_start: newStart.toISOString(), duration: newDuration };

      let changeMsg = null;
      if (finishedMode === "move") {
        if (newRowType === "aircraft" && newResourceId !== String(pill.dataset.aircraftId)) {
          const ac = AIRCRAFT.find((a) => String(a.id) === newResourceId);
          payload.aircraft_id = newResourceId;
          changeMsg = `Move this booking to aircraft ${ac ? ac.reg : newResourceId}?`;
        } else if (newRowType === "instructor" && newResourceId !== String(pill.dataset.instructorId)) {
          const ins = INSTRUCTORS.find((i) => String(i.id) === newResourceId);
          payload.instructor_id = newResourceId;
          changeMsg = `Assign this booking to instructor ${ins ? ins.name : newResourceId}?`;
        }
      }

      const send = (withOverride) => {
        const p = withOverride ? Object.assign({}, payload, { override: "1" }) : payload;
        post(`/api/booking/${pill.dataset.id}/reschedule/`, p).then((res) => {
          if (res.ok && res.data.success) { location.reload(); return; }
          if (res.status === 409 && res.data.blockout && res.data.can_override) {
            const confirmMsg = res.data.soft
              ? res.data.error
              : res.data.error + "\n\nMove it over this block-out anyway?";
            askConfirm(confirmMsg, () => send(true), () => location.reload());
            return;
          }
          toast(res.data.error || "Could not move booking");
          location.reload();
        });
      };

      const origStart    = new Date(pill.dataset.start);
      const origDuration = parseInt(pill.dataset.duration, 10);
      const timeChanged  = newStart.getTime() !== origStart.getTime() || newDuration !== origDuration;
      const fmt = (d) => d.toLocaleString([], { weekday:'short', day:'numeric', month:'short', hour:'2-digit', minute:'2-digit' });
      const isPending    = pill.dataset.status === "pending";
      const notifLine    = isPending
        ? "The member will be notified of the updated booking time."
        : "Any notifications will be re-sent to the member.";

      const proceed = () => {
        if (changeMsg) {
          // Include time change detail when both resource and time shifted
          const timeNote = timeChanged ? `\n\nTime will also move to ${fmt(newStart)}.\n${notifLine}` : "";
          askConfirm(changeMsg + timeNote, () => send(false), () => location.reload());
        } else {
          send(false);
        }
      };

      const proceedWithTypCheck = () => {
        if (isOutsideTypical(newStart, newDuration)) {
          const typMsg = `This booking falls outside typical hours (${cfg.typicalStart}–${cfg.typicalEnd}).\n\nConfirm you intend to book off-hours?`;
          askConfirm(typMsg, proceed, () => location.reload());
        } else {
          proceed();
        }
      };

      if (timeChanged && !changeMsg) {
        askConfirm(`Move booking to ${fmt(newStart)}?\n\n${notifLine}`, proceedWithTypCheck, () => location.reload());
      } else {
        proceedWithTypCheck();
      }
    });

    // pointercancel fires when the browser interrupts the drag (touch gesture
    // take-over, scroll, system UI). Without this handler the ghost elements
    // would stay on the body and the drag state would be corrupted.
    pill.addEventListener("pointercancel", cancelDrag);
  }

  // ---- Block-out creation modal ------------------------------------------
  if (cfg.canManage) {
    const BLOCKOUT_TYPES = JSON.parse(document.getElementById("blockout-types-data").textContent);
    const boModal   = document.getElementById("bo-modal");
    const boType    = document.getElementById("bo-type");
    const boScope   = document.getElementById("bo-scope");
    const boAcRow   = document.getElementById("bo-aircraft-row");
    const boInRow   = document.getElementById("bo-instructor-row");
    const boAcSel   = document.getElementById("bo-aircraft");
    const boInSel   = document.getElementById("bo-instructor");
    const boRecur   = document.getElementById("bo-recurrence");
    const boDateWr  = document.getElementById("bo-date-wrap");
    const boWdWr    = document.getElementById("bo-weekday-wrap");
    const boDate    = document.getElementById("bo-date");
    const boWd      = document.getElementById("bo-weekday");
    const boAllDay  = document.getElementById("bo-allday");
    const boTimeRow = document.getElementById("bo-time-row");
    const boLabel   = document.getElementById("bo-label");

    // Populate type dropdown
    BLOCKOUT_TYPES.forEach((bt) => {
      const o = document.createElement("option");
      o.value = bt.id; o.textContent = bt.name;
      boType.appendChild(o);
    });

    // Populate aircraft/instructor selects
    AIRCRAFT.forEach((a) => {
      const o = document.createElement("option");
      o.value = a.id; o.textContent = `${a.reg} (${a.type})`;
      boAcSel.appendChild(o);
    });
    INSTRUCTORS.forEach((i) => {
      const o = document.createElement("option");
      o.value = i.id; o.textContent = i.name;
      boInSel.appendChild(o);
    });

    function syncBoScope() {
      boAcRow.hidden = boScope.value !== "aircraft";
      boInRow.hidden = boScope.value !== "instructors";
    }
    function syncBoRecur() {
      boDateWr.hidden = boRecur.value !== "one_off";
      boWdWr.hidden   = boRecur.value !== "weekly";
    }
    function syncBoAllDay() {
      boTimeRow.hidden = boAllDay.checked;
    }

    boScope.addEventListener("change", syncBoScope);
    boRecur.addEventListener("change", syncBoRecur);
    boAllDay.addEventListener("change", syncBoAllDay);

    function openBoModal() {
      boDate.value = cfg.selectedDate;
      boLabel.value = "";
      boAllDay.checked = true;
      boTimeRow.hidden = true;
      boScope.value = "all";
      boRecur.value = "one_off";
      syncBoScope(); syncBoRecur();
      boModal.hidden = false;
    }
    function closeBoModal() { boModal.hidden = true; }

    document.getElementById("btn-blockout").addEventListener("click", openBoModal);
    document.getElementById("bo-cancel").addEventListener("click", closeBoModal);
    boModal.addEventListener("click", (e) => { if (e.target === boModal) closeBoModal(); });

    document.getElementById("bo-save").addEventListener("click", () => {
      const data = {
        blockout_type_id: boType.value,
        scope: boScope.value,
        label: boLabel.value,
        recurrence: boRecur.value,
        all_day: boAllDay.checked ? "on" : "",
        date: boDate.value,
        weekday: boWd.value,
        start_time: document.getElementById("bo-start").value,
        end_time: document.getElementById("bo-end").value,
      };
      Array.from(boAcSel.selectedOptions).forEach((o) => data["aircraft_ids"] = [...(data["aircraft_ids"] || []), o.value]);
      Array.from(boInSel.selectedOptions).forEach((o) => data["instructor_ids"] = [...(data["instructor_ids"] || []), o.value]);

      const body = new URLSearchParams();
      Object.entries(data).forEach(([k, v]) => {
        if (Array.isArray(v)) v.forEach((val) => body.append(k, val));
        else body.set(k, v);
      });

      fetch(cfg.blockoutCreateUrl, {
        method: "POST",
        headers: { "X-CSRFToken": cfg.csrf, "Content-Type": "application/x-www-form-urlencoded" },
        body,
      }).then(async (r) => {
        let j = {}; try { j = await r.json(); } catch(e){}
        if (r.ok && j.success) { location.reload(); return; }
        toast(j.error || "Could not save block-out");
      });
    });
  }

  // ---- Detail overlay (booking detail without leaving calendar) ----------
  const detailOverlay = document.getElementById("detail-overlay");
  const detailBody = document.getElementById("detail-overlay-body");
  let overlayDidChange = false;

  function openDetailOverlay(url) {
    // Accept either a full URL or a booking ID
    let detailUrl = url;
    if (/^\d+$/.test(String(url))) {
      detailUrl = cfg.bookingDetailBase.replace("/0/", "/" + url + "/");
    }
    overlayDidChange = false;
    detailOverlay.hidden = false;
    detailBody.innerHTML = '<p style="color:#8a93a0;padding:2.5rem;text-align:center;">Loading…</p>';
    loadDetailOverlay(detailUrl);
  }
  window.openDetailOverlay = openDetailOverlay;
  window.openNewBooking = function() {
    try {
      openCreate(null, new Date(Date.now() + 60 * 1000));
    } catch(e) {
      toast('Could not open booking form: ' + e.message, 'err');
    }
  };
  window.openNewBookingWith = function(aircraftId, instructorId, startIso, endIso, bookingKind, instructorIds) {
    try {
      var start = new Date(startIso);
      // openCreate restores the full instructor list then clears flight type
      openCreate(aircraftId || null, start, null, null);

      // Pick a flight type matching the booking kind so instructor field visibility is correct
      var isSoloKind = (bookingKind === 'solo');
      var matchingFt = FLIGHT_TYPES.find(function(ft) {
        return !ft.for_contacts && (ft.is_solo === isSoloKind);
      });
      if (matchingFt) {
        fFlightType.value = String(matchingFt.id);
        syncFlightTypeMode();
      }
      // For dual bookings, always force instructor field visible regardless of whether a flight
      // type matched (club may only have one generic type; user still needs to pick instructor)
      if (!isSoloKind && fInstructorLabel) {
        fInstructorLabel.hidden = false;
        fInstructor.disabled = false;
        if (fInstructorLabel.style) fInstructorLabel.style.opacity = '';
      }

      // Filter instructor dropdown to only the available instructors for this slot
      if (!isSoloKind && instructorIds && instructorIds.length > 0) {
        var allowed = {};
        instructorIds.forEach(function(id) { allowed[String(id)] = true; });
        Array.from(fInstructor.options).forEach(function(opt) {
          if (opt.value && !allowed[opt.value]) opt.remove();
        });
      }

      // Pre-select instructor (single pre-fill or auto-select when only one option remains)
      if (instructorId) {
        fInstructor.value = String(instructorId);
      } else if (instructorIds && instructorIds.length === 1) {
        fInstructor.value = String(instructorIds[0]);
      }

      // Default member to current user for managers — editable in case booking on behalf of another
      if (cfg.canManage && fMember && !fMember.value) {
        fMember.value = String(cfg.currentUserId);
        updateMemberNotice();
        updateMemberDisplay();
      }

      if (endIso && fDuration) {
        var mins = Math.round((new Date(endIso) - start) / 60000);
        if (mins > 0) { fDuration.value = mins; updatePreview(); }
      }
    } catch(e) {
      toast('Could not open booking form: ' + e.message, 'err');
    }
  };

  // ---- deep-link: ?book=1&aircraft=N&start=ISO&booking_kind=solo|dual&instructor_ids=A,B ----
  if (cfg.canBook) {
    const _bp = new URLSearchParams(location.search);
    if (_bp.get("book") === "1") {
      const _acId     = _bp.get("aircraft") || "";
      const _instrId  = _bp.get("instructor") || "";
      const _startIso = _bp.get("start") || "";
      const _endIso   = _bp.get("span_end") || "";
      const _kind     = _bp.get("booking_kind") || "dual";
      const _idsRaw   = _bp.get("instructor_ids") || "";
      const _instrIds = _idsRaw ? _idsRaw.split(',').filter(Boolean) : null;
      const _dlStart  = new Date(_startIso);
      // Call openCreate directly (it is in scope here) — this is the path that is known
      // to work from the URL deep-link; openNewBookingWith is exported for the overlay path.
      openCreate(_acId || null, _dlStart, null, null);
      // Enhance: set flight type + filter instructor dropdown (only if modal actually opened)
      if (!modal.hidden) {
        const _isSolo = (_kind === 'solo');
        const _ft = FLIGHT_TYPES.find(function(ft) { return !ft.for_contacts && ft.is_solo === _isSolo; });
        if (_ft) { fFlightType.value = String(_ft.id); syncFlightTypeMode(); }
        if (!_isSolo && fInstructorLabel) { fInstructorLabel.hidden = false; fInstructor.disabled = false; }
        if (!_isSolo && _instrIds && _instrIds.length > 0) {
          var _allowed = {};
          _instrIds.forEach(function(id) { _allowed[String(id)] = true; });
          Array.from(fInstructor.options).forEach(function(opt) { if (opt.value && !_allowed[opt.value]) opt.remove(); });
        }
        if (_instrId) { fInstructor.value = String(_instrId); }
        else if (_instrIds && _instrIds.length === 1) { fInstructor.value = String(_instrIds[0]); }
        if (cfg.canManage && fMember && !fMember.value) {
          fMember.value = String(cfg.currentUserId); updateMemberNotice(); updateMemberDisplay();
        }
        if (_endIso && fDuration) {
          var _mins = Math.round((new Date(_endIso) - _dlStart) / 60000);
          if (_mins > 0) { fDuration.value = _mins; updatePreview(); }
        }
      }
      history.replaceState(null, "", location.pathname);
    }
  }

  // ---- row-label click → open detail overlay (instructors & aircraft) -----
  document.querySelectorAll(".row-label[data-detail-url]").forEach((el) => {
    el.addEventListener("click", (e) => {
      e.stopPropagation();
      openDetailOverlay(el.dataset.detailUrl);
    });
  });

  async function loadDetailOverlay(url) {
    const inlineUrl = url + (url.includes("?") ? "&" : "?") + "inline=1";
    try {
      const resp = await fetch(inlineUrl, { credentials: "same-origin" });
      if (!resp.ok) {
        detailBody.innerHTML = `<p style="color:#c0392b;padding:2rem;text-align:center;">Could not load booking details (${resp.status}). <a href="${url}" target="_blank" style="color:inherit;text-decoration:underline;">Open in new tab →</a></p>`;
        return;
      }
      const html = await resp.text();
      detailBody.innerHTML = html;
      // innerHTML doesn't execute <script> tags — re-run them so inline JS (e.g. fee dropdowns) works
      detailBody.querySelectorAll("script").forEach(orig => {
        const s = document.createElement("script");
        s.textContent = orig.textContent;
        try { orig.replaceWith(s); } catch(_e) {}
      });
      attachOverlayForms(url);
    } catch(e) {
      detailBody.innerHTML = `<p style="color:#c0392b;padding:2rem;text-align:center;">Could not load booking details. <a href="${url}" target="_blank" style="color:inherit;text-decoration:underline;">Open in new tab →</a></p>`;
    }
  }

  function attachOverlayForms(baseUrl) {
    detailBody.querySelectorAll("[data-close-overlay]").forEach(btn => {
      btn.addEventListener("click", closeDetailOverlay);
    });
    detailBody.querySelectorAll("form").forEach(f => {
      f.addEventListener("submit", async (e) => {
        e.preventDefault();
        overlayDidChange = true;
        const explicitAction = f.getAttribute("action");
        const resolved = explicitAction ? new URL(explicitAction, baseUrl).href : baseUrl;
        const inlineAction = resolved + (resolved.includes("?") ? "&" : "?") + "inline=1";
        try {
          const resp = await fetch(inlineAction, {
            method: "POST",
            body: new FormData(f),
            credentials: "same-origin",
          });
          const html = await resp.text();
          detailBody.innerHTML = html;
          detailBody.querySelectorAll("script").forEach(orig => {
            const s = document.createElement("script");
            s.textContent = orig.textContent;
            orig.replaceWith(s);
          });
          attachOverlayForms(baseUrl);
        } catch(e) {
          toast("Error submitting — please try again");
        }
      });
    });
  }

  function closeDetailOverlay() {
    detailOverlay.hidden = true;
    if (overlayDidChange) location.reload();
  }

  detailOverlay.addEventListener("click", e => {
    if (e.target === detailOverlay) closeDetailOverlay();
  });
})();
