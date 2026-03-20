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

function getMembership() {
  for (const queue of state.queues) {
    for (const slot of queue.slots) {
      if (slot.occupant === state.nickname) {
        return { queue, slot };
      }
    }
  }
  return null;
}

function renderQueueList() {
  queueList.innerHTML = "";

  state.queues.forEach((queue) => {
    const filled = queue.slots.filter((slot) => slot.occupant).length;
    const button = document.createElement("button");
    button.className = `queue-item${queue.id === state.selectedQueueId ? " active" : ""}`;
    button.innerHTML = `<strong>${queue.name}</strong><small>${filled} / ${queue.slots.length} 참석</small>`;
    button.addEventListener("click", () => {
      state.selectedQueueId = queue.id;
      render();
    });
    queueList.appendChild(button);
  });
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

  queueTitle.textContent = selectedQueue.name;
  const filled = selectedQueue.slots.filter((slot) => slot.occupant).length;
  const lastCallCount = selectedQueue.slots.filter((slot) => slot.lastCall).length;
  queueMeta.innerHTML = `
    <span class="meta-chip">${membership && membership.queue.id === selectedQueue.id ? "내가 참석 중인 파티" : "참석 가능 파티"}</span>
    <span class="meta-chip">정원 ${filled} / ${selectedQueue.slots.length}</span>
    <span class="meta-chip">막판 ${lastCallCount}명</span>
    <span class="meta-chip">마지막 업데이트 ${state.updatedAt || "-"}</span>
  `;
  slotGrid.innerHTML = "";

  selectedQueue.slots.forEach((slot) => {
    const isMine = slot.occupant === state.nickname;
    const occupiedByOther = Boolean(slot.occupant) && !isMine;
    const blockedByMembership = Boolean(membership) && !isMine;
    const statusLabel = slot.lastCall ? "막판" : isMine ? "내 참석" : occupiedByOther ? "참석 완료" : "비어 있음";
    const card = document.createElement("article");
    card.className = `slot-card${isMine ? " mine" : ""}${occupiedByOther ? " full" : ""}${slot.lastCall ? " last-call" : ""}`;

    const button = document.createElement("button");
    button.className = `slot-button${isMine ? " leave-button" : ""}`;
    button.textContent = isMine ? "파티 제외" : "참석";
    button.disabled = occupiedByOther || blockedByMembership || !state.nickname;
    button.addEventListener("click", async () => {
      if (isMine) {
        await leaveQueue();
        return;
      }
      await joinQueue(selectedQueue.id, slot.id);
    });

    const actions = document.createElement("div");
    actions.className = "slot-actions";
    actions.appendChild(button);

    if (isMine) {
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
    slotGrid.appendChild(card);
  });
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
