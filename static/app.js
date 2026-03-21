const state = {
  nickname: localStorage.getItem("partyNickname") || "",
  queues: [],
  selectedQueueId: null,
  events: [],
  updatedAt: null,
};

const queueList = document.getElementById("queueList");
const queueTitle = document.getElementById("queueTitle");
const queueMeta = document.getElementById("queueMeta");
const slotGrid = document.getElementById("slotGrid");
const eventFeed = document.getElementById("eventFeed");
const nicknameBadge = document.getElementById("nicknameBadge");
const nicknameDialog = document.getElementById("nicknameDialog");
const nicknameForm = document.getElementById("nicknameForm");
const nicknameInput = document.getElementById("nicknameInput");
const changeNicknameButton = document.getElementById("changeNicknameButton");
const emptyFeedTemplate = document.getElementById("emptyFeedTemplate");

function formatQueueName(queue) {
  if (queue.id.startsWith("rift-normal")) return queue.name.replace("협곡 일반", "⚔️ 협곡 일반");
  if (queue.id === "rift-flex") return `⚔️ ${queue.name}`;
  if (queue.id.startsWith("aram-normal")) return queue.name.replace("칼바람 일반", "❄️ 칼바람 일반");
  if (queue.id.startsWith("aram-augment")) return queue.name.replace("칼바람 증강", "❄️ 칼바람 증강");
  if (queue.id === "tft-normal") return `🧩 ${queue.name}`;
  if (queue.id === "tft-double-up") return `🧩 ${queue.name}`;
  return queue.name;
}

function getMainSlots(queue) {
  return queue.slots || [];
}

function getWaitlistSlots(queue) {
  return queue.waitlist || [];
}

function countOccupied(slots) {
  return slots.filter((slot) => slot.occupant).length;
}

function isWaitSlot(slot) {
  return slot.id.includes("-wait-");
}

function getMembership() {
  for (const queue of state.queues) {
    for (const slot of [...getMainSlots(queue), ...getWaitlistSlots(queue)]) {
      if (slot.occupant === state.nickname) {
        return { queue, slot };
      }
    }
  }
  return null;
}

function getAvailabilityLabel(selectedQueue, membership) {
  if (!membership) return "참석 가능 파티";
  if (membership.queue.id !== selectedQueue.id) return "다른 파티 참여 중";
  return isWaitSlot(membership.slot) ? "내가 대기 중인 파티" : "내가 참석 중인 파티";
}

function renderQueueList() {
  queueList.innerHTML = "";

  state.queues.forEach((queue) => {
    const filled = countOccupied(getMainSlots(queue));
    const waitCount = countOccupied(getWaitlistSlots(queue));
    const button = document.createElement("button");
    button.className = `queue-item${queue.id === state.selectedQueueId ? " active" : ""}`;
    button.innerHTML = `
      <strong>${formatQueueName(queue)}</strong>
      <small>${filled} / ${getMainSlots(queue).length} 참석 · 대기 ${waitCount} / ${getWaitlistSlots(queue).length}</small>
    `;
    button.addEventListener("click", () => {
      state.selectedQueueId = queue.id;
      render();
    });
    queueList.appendChild(button);
  });
}

function buildSlotCard(selectedQueue, slot, membership) {
  const waitSlot = isWaitSlot(slot);
  const isMine = slot.occupant === state.nickname;
  const occupiedByOther = Boolean(slot.occupant) && !isMine;
  const blockedByMembership = Boolean(membership) && !isMine;
  const statusLabel = waitSlot
    ? isMine
      ? "내 대기"
      : occupiedByOther
        ? "대기 중"
        : "대기 가능"
    : slot.lastCall
      ? "막판"
      : isMine
        ? "내 참석"
        : occupiedByOther
          ? "참석 완료"
          : "비어 있음";

  const card = document.createElement("article");
  card.className = `slot-card${isMine ? " mine" : ""}${occupiedByOther ? " full" : ""}${slot.lastCall ? " last-call" : ""}${waitSlot ? " waiting-slot" : ""}`;

  const primaryButton = document.createElement("button");
  primaryButton.className = `slot-button${isMine ? " leave-button" : ""}`;
  primaryButton.textContent = isMine ? (waitSlot ? "대기 취소" : "파티 제외") : waitSlot ? "대기 등록" : "참석";
  primaryButton.disabled = occupiedByOther || blockedByMembership || !state.nickname;
  primaryButton.addEventListener("click", async () => {
    if (isMine) {
      await leaveQueue();
      return;
    }
    await joinQueue(selectedQueue.id, slot.id);
  });

  const actions = document.createElement("div");
  actions.className = "slot-actions";
  actions.appendChild(primaryButton);

  if (slot.occupant && state.nickname && !isMine) {
    const removeButton = document.createElement("button");
    removeButton.className = "remove-button";
    removeButton.textContent = waitSlot ? "대기 제외" : "파티 제외";
    removeButton.addEventListener("click", async () => {
      await removeQueueMember(slot.occupant, waitSlot);
    });
    actions.appendChild(removeButton);
  }

  if (isMine && !waitSlot) {
    const lastCallButton = document.createElement("button");
    lastCallButton.className = `last-call-button${slot.lastCall ? " active" : ""}`;
    lastCallButton.textContent = slot.lastCall ? "막판 해제" : "막판";
    lastCallButton.addEventListener("click", async () => {
      await updateLastCall(!slot.lastCall);
    });
    actions.appendChild(lastCallButton);
  }

  const name = slot.occupant || "비어 있음";
  card.innerHTML = `<span class="slot-status">${statusLabel}</span><h3>${slot.label}</h3><p>${name}</p>`;
  card.appendChild(actions);
  return card;
}

function buildSlotSection(title, slots, selectedQueue, membership, extraClass = "", sectionClass = "") {
  const section = document.createElement("section");
  section.className = `slot-section ${sectionClass}`.trim();

  const header = document.createElement("div");
  header.className = "slot-section__header";
  header.innerHTML = `
    <h3 class="slot-section__title">${title}</h3>
    <span class="slot-section__meta">${countOccupied(slots)} / ${slots.length}명</span>
  `;

  const grid = document.createElement("div");
  grid.className = `slot-grid ${extraClass} slot-grid--count-${slots.length}`.trim();
  slots.forEach((slot) => {
    grid.appendChild(buildSlotCard(selectedQueue, slot, membership));
  });

  section.appendChild(header);
  section.appendChild(grid);
  return section;
}

function renderQueuePanel() {
  const selectedQueue = state.queues.find((queue) => queue.id === state.selectedQueueId);
  const membership = getMembership();

  if (!selectedQueue) {
    queueTitle.textContent = "파티를 선택해주세요";
    queueMeta.textContent = "";
    slotGrid.innerHTML = "";
    return;
  }

  queueTitle.textContent = formatQueueName(selectedQueue);
  const mainSlots = getMainSlots(selectedQueue);
  const waitlistSlots = getWaitlistSlots(selectedQueue);
  const filled = countOccupied(mainSlots);
  const lastCallCount = mainSlots.filter((slot) => slot.lastCall).length;
  queueMeta.innerHTML = `
    <span class="meta-chip">${getAvailabilityLabel(selectedQueue, membership)}</span>
    <span class="meta-chip">정원 ${filled} / ${mainSlots.length}</span>
    <span class="meta-chip">막판 ${lastCallCount}명</span>
    <span class="meta-chip">마지막 업데이트 ${state.updatedAt || "-"}</span>
  `;
  slotGrid.innerHTML = "";
  slotGrid.appendChild(buildSlotSection("참석 인원", mainSlots, selectedQueue, membership, "slot-grid--party", "slot-section--party"));
  slotGrid.appendChild(buildSlotSection("대기열", waitlistSlots, selectedQueue, membership, "slot-grid--waitlist", "slot-section--waitlist"));
}

function renderEvents() {
  eventFeed.innerHTML = "";
  if (!state.events.length) {
    eventFeed.appendChild(emptyFeedTemplate.content.cloneNode(true));
    return;
  }

  state.events.forEach((event) => {
    const item = document.createElement("div");
    item.className = "event-item";
    item.innerHTML = `<time>${event.time}</time><div>${event.message}</div>`;
    eventFeed.appendChild(item);
  });
}

function renderIdentity() {
  nicknameBadge.textContent = state.nickname || "미설정";
}

function render() {
  renderIdentity();
  renderQueueList();
  renderQueuePanel();
  renderEvents();
}

async function fetchState() {
  const response = await fetch("/api/state");
  const data = await response.json();
  state.queues = data.queues;
  state.events = data.events || [];
  state.updatedAt = data.updatedAt;
  if (!state.selectedQueueId && data.queues.length) {
    state.selectedQueueId = data.queues[0].id;
  }
  render();
}

async function joinQueue(queueId, slotId) {
  try {
    const response = await fetch("/api/join", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ nickname: state.nickname, queueId, slotId }),
    });
    const data = await response.json();
    if (!response.ok) {
      throw new Error(data.error || "참석 처리에 실패했습니다.");
    }
    state.queues = data.queues;
    state.events = data.events || [];
    state.updatedAt = data.updatedAt;
    render();
  } catch (error) {
    flash(error.message);
  }
}

async function leaveQueue() {
  try {
    const response = await fetch("/api/leave", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ nickname: state.nickname }),
    });
    const data = await response.json();
    if (!response.ok) {
      throw new Error(data.error || "파티 제외에 실패했습니다.");
    }
    state.queues = data.queues;
    state.events = data.events || [];
    state.updatedAt = data.updatedAt;
    render();
  } catch (error) {
    flash(error.message);
  }
}

async function removeQueueMember(targetNickname, waitSlot) {
  if (!state.nickname) {
    flash("닉네임을 먼저 입력해주세요.");
    return;
  }

  const prompt = waitSlot
    ? `${targetNickname}님을 대기열에서 제외할까요?`
    : `${targetNickname}님을 파티에서 제외할까요?`;
  if (!window.confirm(prompt)) {
    return;
  }

  try {
    const response = await fetch("/api/remove", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ nickname: state.nickname, targetNickname }),
    });
    const data = await response.json();
    if (!response.ok) {
      throw new Error(data.error || "파티 제외 처리에 실패했습니다.");
    }
    state.queues = data.queues;
    state.events = data.events || [];
    state.updatedAt = data.updatedAt;
    render();
  } catch (error) {
    flash(error.message);
  }
}

async function updateLastCall(enabled) {
  try {
    const response = await fetch("/api/last-call", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ nickname: state.nickname, enabled }),
    });
    const data = await response.json();
    if (!response.ok) {
      throw new Error(data.error || "막판 설정 변경에 실패했습니다.");
    }
    state.queues = data.queues;
    state.events = data.events || [];
    state.updatedAt = data.updatedAt;
    render();
  } catch (error) {
    flash(error.message);
  }
}

function flash(message) {
  const existing = document.querySelector(".flash");
  if (existing) existing.remove();

  const item = document.createElement("div");
  item.className = "flash";
  item.textContent = message;
  document.body.appendChild(item);
  setTimeout(() => item.remove(), 2400);
}

function openNicknameDialog() {
  nicknameInput.value = state.nickname;
  nicknameDialog.showModal();
  nicknameInput.focus();
}

nicknameForm.addEventListener("submit", (event) => {
  event.preventDefault();
  const nickname = nicknameInput.value.trim();
  if (!nickname) {
    flash("닉네임을 입력해주세요.");
    return;
  }
  state.nickname = nickname;
  localStorage.setItem("partyNickname", nickname);
  nicknameDialog.close();
  render();
});

changeNicknameButton.addEventListener("click", openNicknameDialog);

window.addEventListener("DOMContentLoaded", async () => {
  await fetchState();
  if (!state.nickname) {
    openNicknameDialog();
  }
  window.setInterval(fetchState, 3000);
});
