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
    const available = scroll.clientWidth - 132; // 130px label + 2px border
    if (available <= 0 || !cfg0.totalMins) return;
    const ideal = available / cfg0.totalMins;
    if (Math.abs(ideal - cfg0.pxPerMin) / cfg0.pxPerMin > 0.03) {
      params.set("zoom", ideal.toFixed(3));
      location.replace(location.pathname + "?" + params.toString());
    }
  })();

  const cfg = JSON.parse(document.getElementById("cal-data").textContent);
  const MEMBERS = JSON.parse(document.getElementById("members-data").textContent);
  const AIRCRAFT = JSON.parse(document.getElementById("aircraft-data").textContent);
  const INSTRUCTORS = JSON.parse(document.getElementById("instructors-data").textContent);
  const FLIGHT_TYPES = JSON.parse(document.getElementById("flight-types-data").textContent);

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
  function toast(msg) {
    const t = document.getElementById("toast");
    t.textContent = msg;
    t.hidden = false;
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
  const btnDelete = document.getElementById("m-delete");
  const btnConfirm = document.getElementById("m-confirm");
  const btnDepart = document.getElementById("m-depart");
  const btnCheckin = document.getElementById("m-checkin-btn");
  const btnCharges = document.getElementById("m-charges-link");
  const btnWatch = document.getElementById("m-watch");
  const editFields = document.getElementById("m-edit-fields");
  const checkinFields = document.getElementById("m-checkin-fields");
  const declWarn = document.getElementById("m-decl-warn");
  const btnWatchLabel = document.getElementById("m-watch-label");

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
  (function populateMembers() {
    fMember.innerHTML = "";
    MEMBERS.forEach((m) => {
      const o = document.createElement("option");
      o.value = m.id;
      const badge = m.badge === 'current' ? '' : m.badge === 'non_member' ? ' ·Non-mbr' : ' ·Lapsed';
      const acct  = m.acct_warning ? ' ⚠' : '';
      o.textContent = m.name + badge + acct;
      if (m.badge !== 'current') o.style.color = '#adb5bd';
      fMember.appendChild(o);
    });
  })();
  fillSelect(fAircraft, AIRCRAFT, "id", "reg", false);
  fillSelect(fInstructor, INSTRUCTORS, "id", "name", true);
  fillSelect(fFlightType, FLIGHT_TYPES, "id", "name", false);

  const memberNotice = document.getElementById("m-member-notice");
  function updateMemberNotice() {
    const m = MEMBERS.find((x) => String(x.id) === fMember.value);
    if (!m) { memberNotice.style.display = 'none'; return; }
    const msgs = [];
    if (m.badge === 'lapsed')      msgs.push("Member is not current (lapsed or suspended).");
    if (m.badge === 'non_member')  msgs.push("Non-member record — confirm booking is appropriate.");
    if (m.acct_warning)            msgs.push("Account balance is negative. Check with CFI before confirming this booking.");
    if (msgs.length) {
      memberNotice.textContent = msgs.join(' ');
      memberNotice.style.display = 'block';
    } else {
      memberNotice.style.display = 'none';
    }
  }
  fMember.addEventListener("change", updateMemberNotice);

  // Hide member selector for non-staff — they always book for themselves
  if (!cfg.canManage) {
    const memberLabel = fMember.closest("label");
    if (memberLabel) memberLabel.hidden = true;
  }

  function syncInstructorVisibility() {
    const ft = FLIGHT_TYPES.find((x) => String(x.id) === fFlightType.value);
    if (ft && ft.is_solo) {
      fInstructor.value = "";
      fInstructorLabel.hidden = true;
    } else {
      fInstructorLabel.hidden = false;
    }
  }
  fFlightType.addEventListener("change", syncInstructorVisibility);

  function defaultFlightTypeFor(isSolo) {
    if (isSolo) {
      return (FLIGHT_TYPES.find((ft) => ft.is_solo && ft.code === "student_solo")
           || FLIGHT_TYPES.find((ft) => ft.is_solo));
    }
    return (FLIGHT_TYPES.find((ft) => !ft.is_solo && ft.code === "student_dual")
         || FLIGHT_TYPES.find((ft) => !ft.is_solo)
         || FLIGHT_TYPES[0]);
  }

  function openCreate(aircraftId, start, instructorId, bookingKind) {
    // Non-staff cannot book in the past
    if (!cfg.canManage && start < new Date()) {
      toast("Bookings cannot be made in the past");
      return;
    }
    document.getElementById("modal-title").textContent = "New booking";
    fId.value = "";
    btnDelete.hidden = true;
    btnConfirm.hidden = true;
    if (aircraftId) fAircraft.value = aircraftId;
    const isSolo = bookingKind === "solo";
    fInstructor.value = instructorId || "";
    fStart.value = fmtLocalInput(start);
    fDuration.value = cfg.defaultDuration;
    fDesc.value = "";
    const defFt = defaultFlightTypeFor(isSolo);
    if (defFt) fFlightType.value = defFt.id;
    syncInstructorVisibility();
    if (!cfg.canManage) {
      fMember.value = cfg.currentUserId;
    } else if (MEMBERS.length) {
      fMember.value = MEMBERS[0].id;
    }
    updateMemberNotice();
    conflictNotice.hidden = true;
    conflictNotice.innerHTML = "";
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

    // Ghost track = instructor role changed or inactive
    const track = pill.parentElement;
    if (track && track.dataset.ghost === "true") {
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
    document.getElementById("modal-title").textContent = "Edit booking";
    fId.value = pill.dataset.id;
    btnDelete.hidden = false;
    const st = pill.dataset.status;
    const statusNotice = document.getElementById("m-status-notice");
    const statusText = document.getElementById("m-status-text");
    btnConfirm.hidden = !(cfg.canManage && st === "pending");
    btnDepart.hidden = !(cfg.canManage && st === "confirmed");
    // departed: hide edit fields, show check-in panel
    const isDeparted = (st === "departed");
    const isCompleted = (st === "completed");
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
    // declaration warning for confirmed flights
    declWarn.hidden = !(st === "confirmed" && pill.dataset.requiresDeclaration === "true" && pill.dataset.hasDeclaration !== "true");
    // reset check-in fields and mark required based on aircraft config
    if (isDeparted) {
      document.getElementById("m-outcome").value = "completed";
      document.getElementById("m-outcome-notes-wrap").hidden = true;
      ["m-hobbs-start","m-hobbs-end","m-tacho-start","m-tacho-end","m-airswitch-start","m-airswitch-end"].forEach(id => {
        const el = document.getElementById(id); if (el) el.value = "";
      });
      const needHobbs     = pill.dataset.recordsHobbs     === "true";
      const needTacho     = pill.dataset.recordsTacho     === "true";
      const needAirswitch = pill.dataset.recordsAirswitch === "true";
      [["m-hobbs-start","Hobbs start"],["m-hobbs-end","Hobbs end"]].forEach(([id, lbl]) => {
        const el = document.getElementById(id); if (!el) return;
        el.required = needHobbs;
        el.style.borderColor = "";
        el.closest("label").style.opacity = needHobbs ? "1" : ".45";
        el.closest("label").firstChild.textContent = needHobbs ? lbl + " *" : lbl;
      });
      [["m-tacho-start","Tacho start"],["m-tacho-end","Tacho end"]].forEach(([id, lbl]) => {
        const el = document.getElementById(id); if (!el) return;
        el.required = needTacho;
        el.style.borderColor = "";
        el.closest("label").style.opacity = needTacho ? "1" : ".45";
        el.closest("label").firstChild.textContent = needTacho ? lbl + " *" : lbl;
      });
      [["m-airswitch-start","Air switch start"],["m-airswitch-end","Air switch end"]].forEach(([id, lbl]) => {
        const el = document.getElementById(id); if (!el) return;
        el.required = needAirswitch;
        el.style.borderColor = "";
        el.closest("label").style.opacity = needAirswitch ? "1" : ".45";
        el.closest("label").firstChild.textContent = needAirswitch ? lbl + " *" : lbl;
      });
      // Store for validation
      btnCheckin.dataset.needsHobbs     = needHobbs;
      btnCheckin.dataset.needsTacho     = needTacho;
      btnCheckin.dataset.needsAirswitch = needAirswitch;

      // Pre-fill start values from last recorded end for this aircraft
      fetch(`/api/booking/${pill.dataset.id}/prev-readings/`, { credentials: "same-origin" })
        .then(r => r.ok ? r.json() : null)
        .then(data => {
          if (!data) return;
          if (data.hobbs_end     && document.getElementById("m-hobbs-start"))     document.getElementById("m-hobbs-start").value     = data.hobbs_end;
          if (data.tacho_end     && document.getElementById("m-tacho-start"))     document.getElementById("m-tacho-start").value     = data.tacho_end;
          if (data.airswitch_end && document.getElementById("m-airswitch-start")) document.getElementById("m-airswitch-start").value = data.airswitch_end;
        })
        .catch(() => {});
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
    fStart.value = fmtLocalInput(new Date(pill.dataset.start));
    fDuration.value = pill.dataset.duration;
    fDesc.value = pill.dataset.desc || "";
    if (pill.dataset.flightTypeId) fFlightType.value = pill.dataset.flightTypeId;
    syncInstructorVisibility();
    const m = MEMBERS.find((x) => x.name === pill.dataset.member);
    if (m) fMember.value = m.id;
    updateMemberNotice();

    const noticeHtml = buildConflictNotice(pill);
    if (noticeHtml) {
      conflictNotice.innerHTML = noticeHtml;
      conflictNotice.hidden = false;
    } else {
      conflictNotice.hidden = true;
      conflictNotice.innerHTML = "";
    }

    modal.hidden = false;
  }
  const btnSave = document.getElementById("m-save");

  function closeModal() {
    modal.hidden = true;
    removePreview();
    btnConfirm.hidden = true;
    btnDepart.hidden = true;
    btnCheckin.hidden = true;
    btnCharges.hidden = true;
    declWarn.hidden = true;
    document.getElementById("m-status-notice").hidden = true;
    editFields.style.display = "";
    checkinFields.hidden = true;
    btnSave.hidden = false;
    btnDelete.hidden = false;
  }
  document.getElementById("m-cancel").addEventListener("click", closeModal);
  modal.addEventListener("click", (e) => { if (e.target === modal) closeModal(); });

  // Depart button — AJAX
  btnDepart.addEventListener("click", function() {
    const id = document.getElementById("m-booking-id").value;
    if (!id) return;
    const reason = (document.getElementById("m-decl-reason") || {}).value || "";
    const body = {};
    if (reason.trim()) body.no_declaration_reason = reason.trim();
    post(`/api/booking/${id}/depart/`, body).then(res => {
      if (!res.ok) {
        if (res.data && res.data.needs_reason) {
          declWarn.hidden = false;
        } else {
          showToast(res.data && res.data.error ? res.data.error : "Could not depart");
        }
        return;
      }
      // Update ALL pills for this booking (aircraft row + instructor row)
      document.querySelectorAll(`.pill[data-id="${id}"]`).forEach(pill => {
        pill.classList.remove("confirmed","pending");
        pill.classList.add("departed");
        pill.dataset.status = "departed";
        const nm = pill.querySelector(".nm");
        if (nm) nm.textContent = nm.textContent.replace(/^[✈✓⏱] /, "") ;
        if (nm && !nm.textContent.startsWith("✈")) nm.textContent = "✈ " + nm.textContent;
        const sub = pill.querySelector(".sub");
        if (sub) { const t = sub.textContent.replace(/ · .*$/, ""); sub.textContent = t + " · checked out"; }
      });
      closeModal();
      showToast("Booking marked as departed");
    });
  });

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
    if (needsHobbs     && (!hs || !he))  { toast("Hobbs start and end are required"); return; }
    if (needsTacho     && (!ts || !te))  { toast("Tacho start and end are required"); return; }
    if (needsAirswitch && (!as_|| !ae))  { toast("Air switch start and end are required"); return; }
    if (needsHobbs && he && hs && parseFloat(he) <= parseFloat(hs)) { toast("Hobbs end must be greater than start"); return; }
    if (needsTacho && te && ts && parseFloat(te) <= parseFloat(ts)) { toast("Tacho end must be greater than start"); return; }
    if (needsAirswitch && ae && as_ && parseFloat(ae) <= parseFloat(as_)) { toast("Air switch end must be greater than start"); return; }
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
  fStart.addEventListener("input", updatePreview);
  fDuration.addEventListener("input", updatePreview);
  fAircraft.addEventListener("change", updatePreview);

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
          toast(res.data.error || "Could not save booking");
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
  const ccKeep = document.getElementById("cc-keep");
  const ccConfirm = document.getElementById("cc-confirm");

  btnDelete.addEventListener("click", () => {
    if (!fId.value) return;
    cancelReleaseCheck.checked = false;
    cancelChoiceModal.hidden = false;
  });
  ccKeep.addEventListener("click", () => { cancelChoiceModal.hidden = true; });
  ccConfirm.addEventListener("click", () => {
    const id = fId.value;
    const release = cancelReleaseCheck.checked ? "1" : "0";
    cancelChoiceModal.hidden = true;
    post(`/api/booking/${id}/reject/`, { release }).then((res) => {
      if (res.ok && res.data.success) location.reload();
      else toast(res.data.error || "Could not cancel");
    });
  });

  // Pre-confirmation credential check
  const credCheckModal = document.getElementById("cred-check-modal");
  const credCheckBody  = document.getElementById("cred-check-body");
  const credCheckTitle = document.getElementById("cred-check-title");
  const credCheckConfirm = document.getElementById("cred-check-confirm");
  const credCheckCancel  = document.getElementById("cred-check-cancel");
  let _pendingConfirmId = null;

  function doConfirm(id) {
    post(`/api/booking/${id}/confirm/`, {}).then((res) => {
      if (credCheckModal) credCheckModal.hidden = true;
      if (res.ok && res.data.success) location.reload();
      else toast(res.data.error || "Could not confirm booking");
    });
  }

  btnConfirm.addEventListener("click", () => {
    const id = fId.value;
    if (!id) return;
    _pendingConfirmId = id;

    if (!credCheckModal) { doConfirm(id); return; }

    // Fetch credential check before showing confirm
    fetch(`/api/booking/${id}/credential-check/`, { credentials: "same-origin" })
      .then(r => r.json())
      .then(data => {
        const STATUS_ICON = { ok: "✓", warn: "⚠", info: "ℹ" };
        const STATUS_COLOR = { ok: "#2a7a3b", warn: "#c76c00", info: "#2563eb" };

        credCheckTitle.textContent = `Confirm booking — ${data.member}`;
        const rows = data.checks.map(c =>
          `<div style="display:flex;gap:.6rem;align-items:flex-start;padding:.3rem 0;border-bottom:1px solid #f0f2f4;">
             <span style="font-size:.95rem;color:${STATUS_COLOR[c.status] || '#5b6573'};flex-shrink:0;width:18px;">${STATUS_ICON[c.status] || '?'}</span>
             <div>
               <div style="font-size:.84rem;font-weight:600;color:#1f2933;">${c.label}</div>
               <div style="font-size:.8rem;color:#5b6573;">${c.detail}</div>
             </div>
           </div>`
        ).join('');

        credCheckBody.innerHTML = rows ||
          '<p style="color:#8a93a0;font-size:.85rem;">No credential checks configured for this flight type.</p>';

        credCheckConfirm.textContent = data.has_warnings ? "Confirm anyway (staff override)" : "Confirm booking";
        credCheckConfirm.style.background = data.has_warnings ? "#c76c00" : "";
        credCheckConfirm.style.borderColor = data.has_warnings ? "#c76c00" : "";

        credCheckModal.hidden = false;
      })
      .catch(() => { doConfirm(id); });  // fallback if check fails
  });

  if (credCheckConfirm) credCheckConfirm.addEventListener("click", () => { if (_pendingConfirmId) doConfirm(_pendingConfirmId); });
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
        if (e.target.closest(".pill")) return;
        const rect = track.getBoundingClientRect();
        const x = e.clientX - rect.left;
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

  // ---- pill click to edit ---------------------------------------------
  document.querySelectorAll(".pill").forEach((pill) => {
    pill.addEventListener("click", (e) => {
      if (pill._didDrag) { pill._didDrag = false; return; }
      e.stopPropagation();
      if (cfg.canManage) {
        // Completed bookings go straight to the detail overlay — the editor has nothing useful to show
        if (pill.dataset.status === "completed") {
          openDetailOverlay(pill.dataset.id);
        } else {
          openEdit(pill);
        }
      } else if (cfg.canBook) {
        // Non-staff: open watch modal for other members' active bookings
        const isOwn = pill.dataset.memberUserId === String(cfg.currentUserId);
        const isActive = pill.dataset.status !== "cancelled" && pill.dataset.status !== "completed";
        if (!isOwn && isActive) openWatchModal(pill);
      }
    });
  });

  // ---- drag + resize ---------------------------------------------------
  // Cache all tracks once so we can map a Y coordinate to a row reliably,
  // without depending on elementsFromPoint (which is unreliable mid-capture).
  const ALL_TRACKS = Array.from(document.querySelectorAll(".track[data-row-type]:not([data-ghost])"));
  function trackAtY(clientY) {
    for (const t of ALL_TRACKS) {
      const r = t.getBoundingClientRect();
      if (clientY >= r.top && clientY <= r.bottom) return t;
    }
    return null;
  }

  if (cfg.canManage) {
    document.querySelectorAll(".pill").forEach((pill) => makeInteractive(pill));
  }

  function makeInteractive(pill) {
    let mode = null; // 'move' | 'resize'
    let startX, startY, origLeft, origWidth, moved;

    pill.addEventListener("pointerdown", (e) => {
      mode = e.target.hasAttribute("data-resize") ? "resize" : "move";
      startX = e.clientX;
      startY = e.clientY;
      origLeft = parseFloat(pill.style.left);
      origWidth = parseFloat(pill.style.width);
      moved = false;
      pill.setPointerCapture(e.pointerId);
      pill.classList.add("drag");
      e.preventDefault();
    });

    pill.addEventListener("pointermove", (e) => {
      if (!mode) return;
      const dx = e.clientX - startX;
      const dy = e.clientY - startY;
      if (Math.abs(dx) > 3 || Math.abs(dy) > 3) moved = true;

      if (mode === "resize") {
        let w = Math.max(minToPx(SLOT), origWidth + dx);
        pill.style.width = w + "px";
      } else {
        // follow vertically across tracks using cached bounds
        const trackUnder = trackAtY(e.clientY);
        if (trackUnder && trackUnder !== pill.parentElement) {
          trackUnder.appendChild(pill);
        }
        let left = Math.max(0, origLeft + dx);
        pill.style.left = left + "px";
      }
      const t = pill.parentElement;
      const bad = overlapsInTrack(t, parseFloat(pill.style.left), parseFloat(pill.style.width), pill.dataset.id);
      pill.classList.toggle("invalid", bad);
    });

    pill.addEventListener("pointerup", (e) => {
      if (!mode) return;
      pill.releasePointerCapture(e.pointerId);
      pill.classList.remove("drag");
      const finishedMode = mode;
      mode = null;
      if (!moved) { pill._didDrag = false; return; }
      pill._didDrag = true;

      // Resolve the true drop row by Y coordinate (robust, no elementsFromPoint).
      let newTrack = pill.parentElement;
      if (finishedMode === "move") {
        const under = trackAtY(e.clientY);
        if (under) {
          newTrack = under;
          if (under !== pill.parentElement) under.appendChild(pill);
        }
      }

      // snap
      const leftPx = snap(pxToMin(parseFloat(pill.style.left))) * PX;
      const widthPx = snap(pxToMin(parseFloat(pill.style.width))) * PX;
      pill.style.left = leftPx + "px";
      pill.style.width = widthPx + "px";

      if (overlapsInTrack(newTrack, leftPx, widthPx, pill.dataset.id)) {
        toast("That slot is already taken.");
        return location.reload();
      }

      const newStart = startFromLeft(leftPx);
      const newDuration = snap(pxToMin(widthPx));
      const newRowType = newTrack.dataset.rowType;
      const newResourceId = String(newTrack.dataset.resourceId);

      const payload = { new_start: newStart.toISOString(), duration: newDuration };

      // Detect resource change (dragged to a different row)
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

      const proceed = () => {
        if (changeMsg) askConfirm(changeMsg, () => send(false), () => location.reload());
        else send(false);
      };
      if (isOutsideTypical(newStart, newDuration)) {
        const typMsg = `This booking falls outside typical hours (${cfg.typicalStart}–${cfg.typicalEnd}).\n\nConfirm you intend to book off-hours?`;
        askConfirm(typMsg, proceed, () => location.reload());
      } else {
        proceed();
      }
    });
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

  async function loadDetailOverlay(url) {
    const inlineUrl = url + (url.includes("?") ? "&" : "?") + "inline=1";
    try {
      const resp = await fetch(inlineUrl, { credentials: "same-origin" });
      const html = await resp.text();
      detailBody.innerHTML = html;
      // innerHTML doesn't execute <script> tags — re-run them so inline JS (e.g. fee dropdowns) works
      detailBody.querySelectorAll("script").forEach(orig => {
        const s = document.createElement("script");
        s.textContent = orig.textContent;
        orig.replaceWith(s);
      });
      attachOverlayForms(url);
    } catch(e) {
      detailBody.innerHTML = '<p style="color:#c0392b;padding:2rem;text-align:center;">Could not load booking details.</p>';
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
