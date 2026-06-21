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
  const btnConfirm = document.getElementById("m-confirm");
  const btnDepart = document.getElementById("m-depart");
  const btnCheckin = document.getElementById("m-checkin-btn");
  const btnCharges = document.getElementById("m-charges-link");
  const btnDeclLink = document.getElementById("m-decl-btn");
  const mDeclNotice = document.getElementById("m-decl-notice");
  const mDeclNoticeBtn = document.getElementById("m-decl-notice-btn");
  const btnWatch = document.getElementById("m-watch");
  const editFields = document.getElementById("m-edit-fields");
  const checkinFields = document.getElementById("m-checkin-fields");
  const dvView = document.getElementById("m-depart-view");
  const dvCredSection = document.getElementById("dv-cred-section");
  const dvBack = document.getElementById("dv-back");
  const dvConfirm = document.getElementById("dv-confirm");
  const btnWatchLabel = document.getElementById("m-watch-label");
  let _currentPill = null;
  let _departHasCredWarnings = false;

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
    if ((cfg.isInstructor || cfg.canManage) && contactFTs.length) {
      const mg = document.createElement("optgroup"); mg.label = "Member flights";
      memberFTs.forEach(ft => { const o = document.createElement("option"); o.value = ft.id; o.textContent = ft.name; mg.appendChild(o); });
      const cg = document.createElement("optgroup"); cg.label = "Contact flights";
      contactFTs.forEach(ft => { const o = document.createElement("option"); o.value = ft.id; o.textContent = ft.name; cg.appendChild(o); });
      fFlightType.appendChild(mg);
      fFlightType.appendChild(cg);
    } else {
      memberFTs.forEach(ft => { const o = document.createElement("option"); o.value = ft.id; o.textContent = ft.name; fFlightType.appendChild(o); });
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
      fMemberDisplay.style.color = m.badge !== "current" ? "#adb5bd" : "#1f2933";
    } else {
      fMemberDisplay.textContent = "— select member —";
      fMemberDisplay.style.color = "#8a93a0";
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
      post("/api/contact/quick-create/", {
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
    if (_fmSelected) {
      _fmSelected.el.style.background = "";
      _fmSelected.el.style.fontWeight = "";
    }
    _fmSelected = { id: memberId, el: item };
    item.style.background = "color-mix(in srgb, var(--primary) 12%, #fff)";
    item.style.fontWeight  = "600";
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
      item.style.cssText = "padding:.5rem .75rem;cursor:pointer;font-size:.88rem;border-bottom:1px solid #f0f2f4;transition:background .1s;";
      item.style.color = m.badge !== "current" ? "#adb5bd" : "#1f2933";
      item.textContent = m.name + badge + warn;
      item.dataset.memberId = m.id;
      item.addEventListener("click", () => _fmHighlight(item, m.id));
      item.addEventListener("dblclick", () => { _fmHighlight(item, m.id); _fmConfirm(); });
      fmList.appendChild(item);
      count++;
    });
    if (!count) {
      fmList.innerHTML = '<div style="padding:.75rem;color:#8a93a0;font-size:.84rem;text-align:center;">No members found</div>';
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
    btnConfirm.hidden = true;
    if (aircraftId) fAircraft.value = aircraftId;
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
      items.push({
        icon: "⊘",
        title: "Block-out conflict",
        detail: reason.split(";").find(r => r.includes("Overlaps") || r.includes("block-out")) || reason,
        tip: "Move this booking outside the blocked period, or remove the block-out if it was added in error.",
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
    return items.map(it => `
      <div style="padding:.55rem .75rem;border-bottom:1px solid #fca5a5;display:flex;gap:.5rem;align-items:flex-start;">
        <span style="font-size:1rem;line-height:1.3;flex-shrink:0;">${it.icon}</span>
        <div>
          <strong style="font-size:.82rem;color:#c0392b;">${it.title}</strong>
          <div style="color:#7f1d1d;margin:.1rem 0 .2rem;">${it.detail}</div>
          <div style="color:#9a3412;font-style:italic;">Tip: ${it.tip}</div>
        </div>
      </div>`).join("");
  }

  function openEdit(pill) {
    removePreview();
    _currentPill = pill;
    if (dvView) dvView.hidden = true;
    if (dvBack) dvBack.hidden = true;
    if (dvConfirm) dvConfirm.hidden = true;
    document.getElementById("modal-title").textContent = "Edit booking";
    fId.value = pill.dataset.id;
    btnDelete.hidden = false;
    const st = pill.dataset.status;
    const statusNotice = document.getElementById("m-status-notice");
    const statusText = document.getElementById("m-status-text");
    btnConfirm.hidden = !(cfg.canManage && st === "pending");
    btnDepart.hidden = !(cfg.canManage && st === "confirmed");
    const declPendingOnOpen = pill.dataset.declPending === "true";
    if (btnDeclLink) {
      btnDeclLink.hidden = true;  // always hidden in footer; "Open declaration →" is inside the inline panel
      btnDeclLink.dataset.declUrl = pill.dataset.declUrl || "";
    }
    if (mDeclNoticeBtn) mDeclNoticeBtn.dataset.declUrl = pill.dataset.declUrl || "";
    // departed: hide edit fields, show check-in panel
    const isDeparted = (st === "departed");
    const isCompleted = (st === "completed");
    if (mDeclNotice) mDeclNotice.hidden = !(declPendingOnOpen && !isDeparted && !isCompleted);
    editFields.style.display = (isDeparted || isCompleted) ? "none" : "";
    checkinFields.hidden = !isDeparted;
    btnCheckin.hidden = !isDeparted;
    btnSave.hidden = isDeparted || isCompleted;
    btnDelete.hidden = isDeparted || isCompleted;
    // completed: show status notice + view details link
    statusNotice.hidden = !isCompleted;
    if (isCompleted) {
      const isPaid = pill.dataset.paid === "true";
      statusText.textContent = isPaid
        ? "This flight is completed and paid."
        : "Aircraft returned — charges and payment pending.";
    }
    btnCharges.hidden = !isCompleted;
    if (isCompleted) {
      btnCharges.href = cfg.bookingDetailBase.replace("/0/", "/" + pill.dataset.id + "/");
      btnCharges.textContent = "View details →";
      const paid = pill.dataset.paid === "true";
      btnCharges.style.background = paid ? "var(--completed-paid,#7c3aed)" : "var(--returned,#2563eb)";
      btnCharges.style.borderColor = btnCharges.style.background;
    }
    // Check-in title for departed flights
    if (isDeparted) {
      document.getElementById("modal-title").textContent = `Check in — ${pill.dataset.member} · ${pill.dataset.registration}`;
    }
    // reset check-in fields and mark required based on aircraft config
    if (isDeparted) {
      document.getElementById("m-outcome").value = "completed";
      document.getElementById("m-outcome-notes-wrap").hidden = true;
      ["m-hobbs-start","m-hobbs-end","m-tacho-start","m-tacho-end","m-airswitch-start","m-airswitch-end"].forEach(id => {
        const el = document.getElementById(id); if (el) el.value = "";
      });
      const billingMethod = pill.dataset.totalTimeMethod || '';
      const maintMethod   = pill.dataset.maintTimeSource  || '';
      const needHobbs     = pill.dataset.recordsHobbs === "true" || billingMethod === 'hobbs' || maintMethod === 'hobbs';
      const needTacho     = pill.dataset.recordsTacho === "true" || billingMethod === 'tacho' || maintMethod === 'tacho';
      const needAirswitch = pill.dataset.recordsAirswitch === "true" || billingMethod === 'airswitch' || maintMethod === 'airswitch';
      // When multiple instruments are needed, label each with its purpose so instructors know why
      const multiInstrument = (needHobbs ? 1 : 0) + (needTacho ? 1 : 0) + (needAirswitch ? 1 : 0) > 1;
      function instrNote(key) {
        if (!multiInstrument) return "";
        if (billingMethod === key && maintMethod === key) return " (billing/maint)";
        if (billingMethod === key) return " (billing)";
        if (maintMethod   === key) return " (maintenance)";
        return "";
      }
      [["m-hobbs-start","Hobbs start"],["m-hobbs-end","Hobbs end"]].forEach(([id, lbl]) => {
        const el = document.getElementById(id); if (!el) return;
        el.required = needHobbs; el.disabled = !needHobbs; el.value = needHobbs ? el.value : "";
        el.style.borderColor = "";
        el.closest("label").style.opacity = needHobbs ? "1" : ".35";
        el.closest("label").firstChild.textContent = needHobbs ? lbl + " *" + instrNote('hobbs') : lbl;
      });
      [["m-tacho-start","Tacho start"],["m-tacho-end","Tacho end"]].forEach(([id, lbl]) => {
        const el = document.getElementById(id); if (!el) return;
        el.required = needTacho; el.disabled = !needTacho; el.value = needTacho ? el.value : "";
        el.style.borderColor = "";
        el.closest("label").style.opacity = needTacho ? "1" : ".35";
        el.closest("label").firstChild.textContent = needTacho ? lbl + " *" + instrNote('tacho') : lbl;
      });
      [["m-airswitch-start","Air switch start"],["m-airswitch-end","Air switch end"]].forEach(([id, lbl]) => {
        const el = document.getElementById(id); if (!el) return;
        el.required = needAirswitch; el.disabled = !needAirswitch; el.value = needAirswitch ? el.value : "";
        el.style.borderColor = "";
        el.closest("label").style.opacity = needAirswitch ? "1" : ".35";
        el.closest("label").firstChild.textContent = needAirswitch ? lbl + " *" + instrNote('airswitch') : lbl;
      });
      // Store for validation
      btnCheckin.dataset.needsHobbs      = needHobbs;
      btnCheckin.dataset.needsTacho      = needTacho;
      btnCheckin.dataset.needsAirswitch  = needAirswitch;
      btnCheckin.dataset.scheduledHours  = (parseFloat(pill.dataset.duration || '0') / 60).toFixed(2);

      // Lock start fields (protected current readings) and show/hide their amend links
      [['m-hobbs-start', needHobbs], ['m-tacho-start', needTacho], ['m-airswitch-start', needAirswitch]].forEach(([id, needed]) => {
        const inp = document.getElementById(id);
        const lnk = document.getElementById(id + '-amend');
        if (!inp) return;
        if (needed && !inp.disabled) {
          inp.readOnly = true;
          inp.style.background = '#f3f5f7';
          inp.style.cursor = 'default';
          if (lnk) lnk.style.display = '';
        } else {
          if (lnk) lnk.style.display = 'none';
        }
      });
      // Reset error notice and end-field borders
      const checkinErr  = document.getElementById('m-checkin-error');
      const checkinWarn = document.getElementById('m-checkin-warn');
      if (checkinErr)  { checkinErr.hidden  = true; checkinErr.textContent  = ''; }
      if (checkinWarn) { checkinWarn.hidden = true; checkinWarn.textContent = ''; }
      ['m-hobbs-end','m-tacho-end','m-airswitch-end'].forEach(id => {
        const el = document.getElementById(id); if (el) el.style.borderColor = '';
      });

      // Pre-fill start values from last recorded end for this aircraft.
      // If no previous reading exists for a field, unlock it so the user can type directly
      // rather than leaving it locked-empty (which blocks submission).
      fetch(`/api/booking/${pill.dataset.id}/prev-readings/`, { credentials: "same-origin" })
        .then(r => r.ok ? r.json() : null)
        .then(data => {
          function unlockIfEmpty(fieldId) {
            const inp = document.getElementById(fieldId);
            const lnk = document.getElementById(fieldId + '-amend');
            if (inp && inp.readOnly && !inp.value) {
              inp.readOnly = false; inp.style.background = ''; inp.style.cursor = '';
              if (lnk) lnk.style.display = 'none';
            }
          }
          if (!data) {
            unlockIfEmpty('m-hobbs-start'); unlockIfEmpty('m-tacho-start'); unlockIfEmpty('m-airswitch-start');
            return;
          }
          if (data.hobbs_end)     { const el = document.getElementById("m-hobbs-start");     if (el) el.value = data.hobbs_end; }
          else unlockIfEmpty('m-hobbs-start');
          if (data.tacho_end)     { const el = document.getElementById("m-tacho-start");     if (el) el.value = data.tacho_end; }
          else unlockIfEmpty('m-tacho-start');
          if (data.airswitch_end) { const el = document.getElementById("m-airswitch-start"); if (el) el.value = data.airswitch_end; }
          else unlockIfEmpty('m-airswitch-start');
        })
        .catch(() => {
          // Network error — unlock all start fields so check-in is still possible
          ['m-hobbs-start', 'm-tacho-start', 'm-airswitch-start'].forEach(id => {
            const inp = document.getElementById(id);
            const lnk = document.getElementById(id + '-amend');
            if (inp && inp.readOnly) { inp.readOnly = false; inp.style.background = ''; inp.style.cursor = ''; }
            if (lnk) lnk.style.display = 'none';
          });
        });
    }

    // Watch button: show for staff viewing someone else's active booking
    const isOwn = pill.dataset.memberUserId === String(cfg.currentUserId);
    const isActive = pill.dataset.status !== "cancelled" && pill.dataset.status !== "completed";
    if (!isOwn && isActive) {
      const watching = watchedIds.has(pill.dataset.id);
      btnWatchLabel.textContent = watching ? "Watching ✓" : "Watch slot";
      btnWatch.style.background = watching ? "var(--confirmed)" : "";
      btnWatch.style.color = watching ? "#fff" : "";
      btnWatch.style.borderColor = watching ? "var(--confirmed)" : "";
      btnWatch.hidden = false;
    } else {
      btnWatch.hidden = true;
    }
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
    btnConfirm.hidden = true;
    btnDepart.hidden = true;
    if (btnDeclLink) btnDeclLink.hidden = true;
    if (mDeclNotice) mDeclNotice.hidden = true;
    btnCheckin.hidden = true;
    btnCharges.hidden = true;
    document.getElementById("m-status-notice").hidden = true;
    editFields.style.display = "";
    checkinFields.hidden = true;
    btnSave.hidden = false;
    btnDelete.hidden = false;
    if (dvView) dvView.hidden = true;
    if (dvBack) dvBack.hidden = true;
    if (dvConfirm) dvConfirm.hidden = true;
  }
  document.getElementById("m-cancel").addEventListener("click", closeModal);
  modal.addEventListener("click", (e) => { if (e.target === modal) closeModal(); });

  // Check-in button — AJAX
  btnCheckin.addEventListener("click", function() {
    const id = document.getElementById("m-booking-id").value;
    if (!id) return;
    // Validate required meter readings
    const needsHobbs     = this.dataset.needsHobbs     === "true";
    const needsTacho     = this.dataset.needsTacho     === "true";
    const needsAirswitch = this.dataset.needsAirswitch === "true";
    const hs = document.getElementById("m-hobbs-start").value.trim();
    const he = document.getElementById("m-hobbs-end").value.trim();
    const ts = document.getElementById("m-tacho-start").value.trim();
    const te = document.getElementById("m-tacho-end").value.trim();
    const as_ = (document.getElementById("m-airswitch-start") || {}).value?.trim() || "";
    const ae  = (document.getElementById("m-airswitch-end")   || {}).value?.trim() || "";
    const _ciErr = document.getElementById('m-checkin-error');
    const _ciErrs = [];
    ['m-hobbs-end','m-tacho-end','m-airswitch-end'].forEach(id => { const el = document.getElementById(id); if (el) el.style.borderColor = ''; });
    function _ciFail(msg, endId) {
      if (endId) { const el = document.getElementById(endId); if (el) el.style.borderColor = '#e03131'; }
      _ciErrs.push(msg);
    }
    if (needsHobbs && !hs)  _ciFail('Hobbs start is required');
    if (needsHobbs && !he)  _ciFail('Hobbs end is required', 'm-hobbs-end');
    if (needsTacho && !ts)  _ciFail('Tacho start is required');
    if (needsTacho && !te)  _ciFail('Tacho end is required', 'm-tacho-end');
    if (needsAirswitch && !as_) _ciFail('Air switch start is required');
    if (needsAirswitch && !ae)  _ciFail('Air switch end is required', 'm-airswitch-end');
    if (needsHobbs && hs && he && parseFloat(he) <= parseFloat(hs)) _ciFail('Hobbs end must be greater than start', 'm-hobbs-end');
    if (needsTacho && ts && te && parseFloat(te) <= parseFloat(ts)) _ciFail('Tacho end must be greater than start', 'm-tacho-end');
    if (needsAirswitch && as_ && ae && parseFloat(ae) <= parseFloat(as_)) _ciFail('Air switch end must be greater than start', 'm-airswitch-end');
    if (_ciErrs.length) {
      if (_ciErr) { _ciErr.textContent = _ciErrs.join(' · '); _ciErr.hidden = false; }
      else showToast(_ciErrs[0], 'err');
      return;
    }
    if (_ciErr) _ciErr.hidden = true;
    const _ciWarn = document.getElementById('m-checkin-warn');
    if (_ciWarn) _ciWarn.hidden = true;
    const body = {
      outcome:          document.getElementById("m-outcome").value,
      outcome_notes:    document.getElementById("m-outcome-notes").value,
      hobbs_start:      hs,
      hobbs_end:        he,
      tacho_start:      ts,
      tacho_end:        te,
      airswitch_start:  as_,
      airswitch_end:    ae,
    };
    post(`/api/booking/${id}/checkin/`, body).then(res => {
      if (!res.ok) { showToast(res.data && res.data.error ? res.data.error : "Could not check in"); return; }
      document.querySelectorAll(`.pill[data-id="${id}"]`).forEach(pill => {
        pill.classList.remove("departed","confirmed","pending","paid");
        pill.classList.add("completed");
        pill.dataset.status = "completed";
        pill.dataset.paid = "false";
        const nm = pill.querySelector(".nm");
        if (nm) nm.textContent = nm.textContent.replace(/^[✈✓⏱] /, "⏱ ");
        const sub = pill.querySelector(".sub");
        if (sub) { const t = sub.textContent.replace(/ · .*$/, ""); sub.textContent = t + " · returned"; }
        pill.querySelectorAll(".pill-wedge").forEach(w => w.remove());
        const wedge = document.createElement("div");
        wedge.className = "pill-wedge ret";
        wedge.innerHTML = '<i class="ti ti-plane-arrival" aria-hidden="true"></i>';
        pill.appendChild(wedge);
      });
      closeModal();
      showToast("Flight returned — charges ready");
      // Offer to open charges overlay
      if (res.data && res.data.charges_url) {
        setTimeout(() => openDetailOverlay(res.data.charges_url), 400);
      }
    });
  });

  function toggleOutcomeNotes() {
    const v = document.getElementById("m-outcome").value;
    document.getElementById("m-outcome-notes-wrap").hidden = (v === "completed");
  }
  // Amend links — unlock locked start fields
  ['hobbs','tacho','airswitch'].forEach(key => {
    const lnk = document.getElementById(`m-${key}-start-amend`);
    const inp = document.getElementById(`m-${key}-start`);
    if (!lnk || !inp) return;
    lnk.addEventListener('click', e => {
      e.preventDefault();
      inp.readOnly = false;
      inp.style.background = '';
      inp.style.cursor = '';
      lnk.style.display = 'none';
      inp.focus();
    });
  });

  // Instrument reading blur: round to 1dp; validate end > start; warn if delta > 1.3× scheduled
  const _endStartMap = {'m-hobbs-end':'m-hobbs-start','m-tacho-end':'m-tacho-start','m-airswitch-end':'m-airswitch-start'};
  const _instrLabels = {'m-hobbs-end':'Hobbs','m-tacho-end':'Tacho','m-airswitch-end':'Air switch'};
  function _refreshCheckinWarnings() {
    const warnEl = document.getElementById('m-checkin-warn');
    if (!warnEl) return;
    const scheduledHours = parseFloat(btnCheckin.dataset.scheduledHours || '0');
    const warns = [];
    Object.entries(_endStartMap).forEach(([endId, startId]) => {
      const endEl = document.getElementById(endId);
      const startEl = document.getElementById(startId);
      if (!endEl || !startEl || !endEl.value || !startEl.value) return;
      const delta = parseFloat(endEl.value) - parseFloat(startEl.value);
      if (scheduledHours > 0 && delta > 0 && delta > scheduledHours * 1.3) {
        endEl.style.borderColor = '#f59e0b';
        warns.push(`${_instrLabels[endId]} reading (${delta.toFixed(1)}h) is more than 1.3× the scheduled duration`);
      } else if (endEl.style.borderColor === '#f59e0b') {
        endEl.style.borderColor = '';
      }
    });
    warnEl.textContent = warns.join(' · ');
    warnEl.hidden = warns.length === 0;
  }

  ["m-hobbs-start","m-hobbs-end","m-tacho-start","m-tacho-end","m-airswitch-start","m-airswitch-end"].forEach(id => {
    const el = document.getElementById(id);
    if (!el) return;
    el.addEventListener("blur", () => {
      if (el.value !== "") el.value = parseFloat(el.value).toFixed(1);
      const startId = _endStartMap[id];
      if (startId) {
        const startEl = document.getElementById(startId);
        if (el.value && startEl && startEl.value) {
          const isLow = parseFloat(el.value) <= parseFloat(startEl.value);
          el.style.borderColor = isLow ? '#e03131' : '';
        } else {
          el.style.borderColor = '';
        }
      }
      _refreshCheckinWarnings();
    });
  });
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
    const url = id ? `/api/booking/${id}/edit/` : cfgCreateUrl();
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
  function cfgCreateUrl() { return "/api/booking/create/"; }

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

  // Credential check modal — shared by Confirm booking and Check out actions
  const credCheckModal = document.getElementById("cred-check-modal");
  const credCheckBody  = document.getElementById("cred-check-body");
  const credCheckTitle = document.getElementById("cred-check-title");
  const credCheckConfirm = document.getElementById("cred-check-confirm");
  const credCheckCancel  = document.getElementById("cred-check-cancel");
  let _pendingCredId = null;
  let _pendingCredAction = null;  // 'confirm' | 'depart'

  function doConfirm(id) {
    post(`/api/booking/${id}/confirm/`, {}).then((res) => {
      if (credCheckModal) credCheckModal.hidden = true;
      if (res.ok && res.data.success) location.reload();
      else toast(res.data.error || "Could not confirm booking", 'err');
    });
  }

  function doDepart(id, body) {
    post(`/api/booking/${id}/depart/`, body || {}).then(res => {
      if (!res.ok) {
        if (dvConfirm) { dvConfirm.disabled = false; dvConfirm.textContent = "Confirm check out"; }
        showToast(res.data && res.data.error ? res.data.error : "Could not depart", 'err');
        return;
      }
      document.querySelectorAll(`.pill[data-id="${id}"]`).forEach(pill => {
        pill.classList.remove("confirmed", "pending");
        pill.classList.add("departed");
        pill.dataset.status = "departed";
        const nm = pill.querySelector(".nm");
        if (nm) nm.textContent = nm.textContent.replace(/^[✈✓⏱] /, "");
        if (nm && !nm.textContent.startsWith("✈")) nm.textContent = "✈ " + nm.textContent;
        const sub = pill.querySelector(".sub");
        if (sub) { const t = sub.textContent.replace(/ · .*$/, ""); sub.textContent = t + " · airborne"; }
        pill.querySelectorAll(".pill-dot-decl,.pill-wedge").forEach(el => el.remove());
        const wedge = document.createElement("div");
        wedge.className = "pill-wedge dep";
        wedge.innerHTML = '<i class="ti ti-plane-departure" aria-hidden="true"></i>';
        pill.appendChild(wedge);
      });
      closeModal();
      showToast("Booking marked as departed");
    });
  }

  function showCredCheckModal(id, action) {
    if (!credCheckModal) {
      if (action === 'confirm') doConfirm(id);
      return;
    }
    _pendingCredId = id;
    _pendingCredAction = action;

    const actionLabel = action === 'depart' ? 'Check out' : 'Confirm booking';
    credCheckBody.innerHTML = '<p style="color:#8a93a0;font-size:.85rem;padding:.5rem 0;">Checking…</p>';
    credCheckConfirm.textContent = actionLabel;
    credCheckConfirm.style.background = '';
    credCheckConfirm.style.borderColor = '';
    credCheckModal.hidden = false;

    fetch(`/api/booking/${id}/credential-check/`, { credentials: "same-origin" })
      .then(r => r.json())
      .then(data => {
        const STATUS_ICON  = { ok: "✓", warn: "⚠", info: "ℹ" };
        const STATUS_COLOR = { ok: "#2a7a3b", warn: "#c76c00", info: "#2563eb" };
        const URGENCY_BAR  = { green: "#4caf50", amber: "#ff9800", red: "#e03131" };

        credCheckTitle.textContent = `${action === 'depart' ? 'Check out' : 'Confirm booking'} — ${data.member}`;

        // Member credential checks
        const memberRows = (data.checks || []).map(c =>
          `<div style="display:flex;gap:.6rem;align-items:flex-start;padding:.3rem 0;border-bottom:1px solid #f0f2f4;">
             <span style="font-size:.95rem;color:${STATUS_COLOR[c.status] || '#5b6573'};flex-shrink:0;width:18px;">${STATUS_ICON[c.status] || '?'}</span>
             <div>
               <div style="font-size:.84rem;font-weight:600;color:#1f2933;">${c.label}</div>
               <div style="font-size:.8rem;color:#5b6573;">${c.detail}</div>
             </div>
           </div>`
        ).join('');

        // Aircraft maintenance — only AMBER/RED items shown; all-green gets a summary tick
        const allMaint = data.maintenance || [];
        const warnMaint = allMaint.filter(m => m.urgency === 'amber' || m.urgency === 'red');
        const maintRows = warnMaint.map(m => {
          const barColor = URGENCY_BAR[m.urgency] || '#4caf50';
          const pct = m.progress_pct !== null ? m.progress_pct : null;
          const bar = pct !== null
            ? `<div style="height:5px;background:#eef1f4;border-radius:3px;margin-top:.3rem;overflow:hidden;">
                 <div style="width:${pct}%;height:100%;background:${barColor};border-radius:3px;transition:width .3s;"></div>
               </div>`
            : '';
          return `<div style="padding:.3rem 0;border-bottom:1px solid #f0f2f4;">
                    <div style="display:flex;justify-content:space-between;align-items:baseline;">
                      <span style="font-size:.84rem;font-weight:600;color:#1f2933;">${m.name}</span>
                      <span style="font-size:.76rem;color:${barColor};font-weight:600;">${m.detail}</span>
                    </div>
                    ${bar}
                  </div>`;
        }).join('');
        const maintAllClear = allMaint.length > 0 && warnMaint.length === 0
          ? `<div style="font-size:.84rem;color:#2a7a3b;padding:.3rem 0;">✓ No maintenance concerns</div>`
          : '';

        const hobbsNote = data.current_hobbs !== null && data.current_hobbs !== undefined
          ? `<div style="font-size:.78rem;color:#8a93a0;margin-top:.5rem;">Last recorded Hobbs: <strong>${data.current_hobbs}</strong></div>`
          : '';

        let html = '';
        if (memberRows) {
          html += `<div style="font-size:.68rem;font-weight:700;text-transform:uppercase;letter-spacing:.08em;color:#8a93a0;padding:.4rem 0 .2rem;">Pilot — ${data.member}</div>${memberRows}`;
        }
        if (allMaint.length > 0) {
          html += `<div style="font-size:.68rem;font-weight:700;text-transform:uppercase;letter-spacing:.08em;color:#8a93a0;padding:.6rem 0 .2rem;">Aircraft — ${data.aircraft_reg || ''}</div>${maintRows}${maintAllClear}${hobbsNote}`;
        }
        const hasWarnings = data.has_warnings || data.has_maint_warnings;

        let overrideInputHtml = '';
        if (action === 'depart' && hasWarnings) {
          overrideInputHtml = `<div style="margin-top:.8rem;padding:.65rem .75rem;background:#fff8e6;border:1px solid #fcd34d;border-radius:7px;">
            <label style="display:block;font-size:.8rem;font-weight:600;color:#92400e;margin-bottom:.35rem;">Override reason <span style="color:#c0392b;">*</span></label>
            <input type="text" id="cred-check-override-reason"
                   style="display:block;width:100%;padding:.38rem .5rem;border:2px solid #f59e0b;border-radius:5px;font-size:.88rem;box-sizing:border-box;background:#fffbeb;"
                   placeholder="e.g. Flying with instructor, emergency crew duty…">
          </div>`;
        }

        credCheckBody.innerHTML = (html ||
          '<p style="color:#8a93a0;font-size:.85rem;">No checks found.</p>') + overrideInputHtml;

        const overrideLabel = action === 'depart' ? 'Check out anyway (staff override)' : 'Confirm anyway (staff override)';
        credCheckConfirm.textContent = hasWarnings ? overrideLabel : actionLabel;
        credCheckConfirm.style.background = hasWarnings ? "#c76c00" : "";
        credCheckConfirm.style.borderColor = hasWarnings ? "#c76c00" : "";
      })
      .catch(() => {
        if (action === 'confirm') doConfirm(id);
      });
  }

  function openDeclOverlay(url) {
    if (!url) url = btnDeclLink ? btnDeclLink.dataset.declUrl : "";
    if (!url) return;
    openDetailOverlay(url);
  }
  if (btnDeclLink) btnDeclLink.addEventListener("click", () => openDeclOverlay());
  if (mDeclNoticeBtn) mDeclNoticeBtn.addEventListener("click", () => openDeclOverlay(mDeclNoticeBtn.dataset.declUrl));

  const dvDeclOpenBtn = document.getElementById("dv-decl-open");
  if (dvDeclOpenBtn) dvDeclOpenBtn.addEventListener("click", () => openDeclOverlay(dvDeclOpenBtn.dataset.declUrl));

  function openDepartView(pill) {
    const id = pill.dataset.id;
    const reg = pill.dataset.registration;
    const declPending = pill.dataset.declPending === "true";
    const declUrl = pill.dataset.declUrl || "";

    // Use currently-selected member (may have been changed in the edit dialog)
    const selMember = MEMBERS.find(m => String(m.id) === String(fMember.value));
    const memberName = selMember ? selMember.name : pill.dataset.member;
    document.getElementById("modal-title").textContent = `Check out — ${memberName} · ${reg}`;

    editFields.style.display = "none";
    btnWatch.hidden = true;
    btnDelete.hidden = true;
    btnConfirm.hidden = true;
    if (btnDeclLink) btnDeclLink.hidden = true;
    btnDepart.hidden = true;
    btnCheckin.hidden = true;
    btnCharges.hidden = true;
    btnSave.hidden = true;
    document.getElementById("m-status-notice").hidden = true;

    if (dvView) dvView.hidden = false;
    if (dvBack) dvBack.hidden = false;
    if (dvConfirm) { dvConfirm.hidden = false; dvConfirm.disabled = false; dvConfirm.textContent = "Confirm check out"; }

    const dvDeclSection = document.getElementById("dv-decl-section");
    const dvDeclReason  = document.getElementById("dv-decl-reason");
    const dvOverrideSec = document.getElementById("dv-override-section");
    const dvOverrideReason = document.getElementById("dv-override-reason");
    if (dvDeclSection) dvDeclSection.hidden = !declPending;
    if (dvDeclReason)  dvDeclReason.value = "";
    if (dvDeclOpenBtn) dvDeclOpenBtn.dataset.declUrl = declUrl;
    if (dvOverrideSec) dvOverrideSec.hidden = true;
    if (dvOverrideReason) dvOverrideReason.value = "";
    _departHasCredWarnings = false;

    if (dvCredSection) dvCredSection.innerHTML = '<p style="color:#8a93a0;font-size:.84rem;padding:.3rem 0;">Checking credentials and maintenance…</p>';

    fetch(`/api/booking/${id}/credential-check/`, { credentials: "same-origin" })
      .then(r => r.json())
      .then(data => {
        _departHasCredWarnings = !!(data.has_warnings || data.has_maint_warnings);

        const STATUS_ICON  = { ok: "✓", warn: "⚠", info: "ℹ" };
        const STATUS_COLOR = { ok: "#2a7a3b", warn: "#c76c00", info: "#2563eb" };
        const URGENCY_BAR  = { green: "#4caf50", amber: "#ff9800", red: "#e03131" };

        const memberRows = (data.checks || []).map(c =>
          `<div style="display:flex;gap:.6rem;align-items:flex-start;padding:.25rem 0;border-bottom:1px solid #f0f2f4;">
             <span style="font-size:.9rem;color:${STATUS_COLOR[c.status] || '#5b6573'};flex-shrink:0;width:18px;">${STATUS_ICON[c.status] || '?'}</span>
             <div>
               <div style="font-size:.83rem;font-weight:600;color:#1f2933;">${c.label}</div>
               <div style="font-size:.79rem;color:#5b6573;">${c.detail}</div>
             </div>
           </div>`
        ).join('');

        const allMaint = data.maintenance || [];
        const warnMaint = allMaint.filter(m => m.urgency === 'amber' || m.urgency === 'red');
        const maintRows = warnMaint.map(m => {
          const barColor = URGENCY_BAR[m.urgency] || '#4caf50';
          const pct = m.progress_pct !== null ? m.progress_pct : null;
          const bar = pct !== null
            ? `<div style="height:5px;background:#eef1f4;border-radius:3px;margin-top:.25rem;overflow:hidden;"><div style="width:${pct}%;height:100%;background:${barColor};border-radius:3px;"></div></div>`
            : '';
          return `<div style="padding:.25rem 0;border-bottom:1px solid #f0f2f4;">
                    <div style="display:flex;justify-content:space-between;align-items:baseline;">
                      <span style="font-size:.83rem;font-weight:600;color:#1f2933;">${m.name}</span>
                      <span style="font-size:.75rem;color:${barColor};font-weight:600;">${m.detail}</span>
                    </div>${bar}
                  </div>`;
        }).join('');

        const maintAllClear = allMaint.length > 0 && warnMaint.length === 0
          ? `<div style="font-size:.83rem;color:#2a7a3b;padding:.25rem 0;">✓ No maintenance concerns</div>`
          : '';
        const hobbsNote = data.current_hobbs !== null && data.current_hobbs !== undefined
          ? `<div style="font-size:.77rem;color:#8a93a0;margin-top:.4rem;">Last recorded Hobbs: <strong>${data.current_hobbs}</strong></div>`
          : '';

        let html = '';
        if (memberRows) html += `<div style="font-size:.68rem;font-weight:700;text-transform:uppercase;letter-spacing:.08em;color:#8a93a0;padding:.35rem 0 .15rem;">Pilot — ${data.member}</div>${memberRows}`;
        if (allMaint.length > 0) html += `<div style="font-size:.68rem;font-weight:700;text-transform:uppercase;letter-spacing:.08em;color:#8a93a0;padding:.5rem 0 .15rem;">Aircraft — ${data.aircraft_reg || ''}</div>${maintRows}${maintAllClear}${hobbsNote}`;
        if (!html) html = '<p style="color:#2a7a3b;font-size:.84rem;padding:.3rem 0;">✓ All checks passed.</p>';
        if (dvCredSection) dvCredSection.innerHTML = html;

        if (dvOverrideSec) dvOverrideSec.hidden = !_departHasCredWarnings;
      })
      .catch(() => {
        if (dvCredSection) dvCredSection.innerHTML = '<p style="color:#8a93a0;font-size:.84rem;">Could not load checks — you may proceed.</p>';
      });
  }

  if (dvBack) dvBack.addEventListener("click", () => {
    if (_currentPill) openEdit(_currentPill);
  });

  if (dvConfirm) dvConfirm.addEventListener("click", () => {
    if (!_currentPill) { showToast("Error: no booking selected"); return; }
    const id = _currentPill.dataset.id;
    const body = {};

    const dvDeclSection = document.getElementById("dv-decl-section");
    const dvDeclReason  = document.getElementById("dv-decl-reason");
    if (dvDeclSection && !dvDeclSection.hidden) {
      const reason = dvDeclReason ? dvDeclReason.value.trim() : "";
      if (!reason) {
        if (dvDeclReason) { dvDeclReason.style.borderColor = "#e03131"; dvDeclReason.focus(); }
        showToast("Enter a declaration override reason before checking out");
        return;
      }
      body.no_declaration_reason = reason;
      if (dvDeclReason) dvDeclReason.style.borderColor = "";
    }

    if (_departHasCredWarnings) {
      const dvOverrideReason = document.getElementById("dv-override-reason");
      const reason = dvOverrideReason ? dvOverrideReason.value.trim() : "";
      if (!reason) {
        if (dvOverrideReason) { dvOverrideReason.style.borderColor = "#e03131"; dvOverrideReason.focus(); }
        showToast("Enter a credential override reason before checking out");
        return;
      }
      body.eligibility_override_reason = reason;
      if (dvOverrideReason) dvOverrideReason.style.borderColor = "";
    }

    dvConfirm.disabled = true;
    dvConfirm.textContent = "Checking out…";
    // Include current member selection so checkout always uses the displayed member
    body.member_user_id = fMember.value;
    doDepart(id, body);
  });

  btnConfirm.addEventListener("click", () => {
    const id = fId.value;
    if (!id) return;
    showCredCheckModal(id, 'confirm');
  });

  btnDepart.addEventListener("click", () => {
    if (!_currentPill) return;
    openDepartView(_currentPill);
  });

  if (credCheckConfirm) credCheckConfirm.addEventListener("click", () => {
    if (!_pendingCredId) return;
    doConfirm(_pendingCredId);
  });
  if (credCheckCancel)  credCheckCancel.addEventListener("click",  () => { if (credCheckModal) credCheckModal.hidden = true; });
  if (credCheckModal) credCheckModal.addEventListener("click", e => { if (e.target === credCheckModal) credCheckModal.hidden = true; });

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

  // ---- deep-link: ?book=1&aircraft=N&instructor=N&start=ISO ----------
  if (cfg.canBook) {
    const params = new URLSearchParams(location.search);
    if (params.get("book") === "1") {
      const acId = params.get("aircraft") || "";
      const instrId = params.get("instructor") || "";
      const startIso = params.get("start") || "";
      const bookingKind = params.get("booking_kind") || "dual";
      const start = startIso ? new Date(startIso) : new Date(cfg.dayStart);
      openCreate(acId, start, instrId, bookingKind);
      history.replaceState(null, "", location.pathname);
    }
  }

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

  // Watch button inside the staff edit modal
  btnWatch.addEventListener("click", () => {
    const id = fId.value;
    if (!id) return;
    post(`/api/booking/${id}/watch/`, {}).then((res) => {
      if (!res.ok) { toast(res.data.error || "Could not update watch"); return; }
      const watching = res.data.watching;
      if (watching) watchedIds.add(id); else watchedIds.delete(id);
      btnWatchLabel.textContent = watching ? "Watching ✓" : "Watch slot";
      btnWatch.style.background = watching ? "var(--confirmed)" : "";
      btnWatch.style.color = watching ? "#fff" : "";
      btnWatch.style.borderColor = watching ? "var(--confirmed)" : "";
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
        if (pill.dataset.status === "completed") {
          const detailUrl = cfg.bookingDetailBase.replace("/0/", "/" + pill.dataset.id + "/");
          if (window.openPageOverlay) window.openPageOverlay(detailUrl);
          else window.location.href = detailUrl;
        } else {
          openEdit(pill);
        }
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
    // Completed pills are not draggable — skip makeInteractive so click fires naturally
    document.querySelectorAll(".pill").forEach((pill) => {
      if (pill.dataset.status !== "completed") makeInteractive(pill);
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
        showToast("Can't move a departed flight");
        pill.classList.add("shake");
        pill.addEventListener("animationend", () => pill.classList.remove("shake"), { once: true });
        return;
      }
      if (mode === "move" && pill.dataset.status === "completed") {
        mode = null; // reset so pointerup won't set _didDrag and block the click
        return;
      }
      const rect = pill.getBoundingClientRect();
      cursorOffsetX = e.clientX - rect.left; // capture where in the pill was clicked
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
      e.preventDefault();
    });

    pill.addEventListener("pointermove", (e) => {
      if (!mode) return;
      if (!moved && (Math.abs(e.clientX - startX) > 3 || Math.abs(e.clientY - startY) > 3)) moved = true;
      if (!moved) return;

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

      fetch("/api/blockout/create/", {
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

  // Intercept "View details →" link on completed bookings
  btnCharges.addEventListener("click", e => {
    if (!btnCharges.hidden) {
      e.preventDefault();
      const bookingId = fId.value;
      if (bookingId) openDetailOverlay(bookingId);
    }
  });
})();
