const TALK_OPEN = 'drive-talk-open';
const TALK_WIDE = '(min-width: 1200px)';
const talk = { draft: null };

function talkUrl(links) {
  const here = ['localhost', '127.0.0.1'].includes(location.hostname);
  return `${here ? links.url : links.tailnet ?? links.url}?thread=${links.thread}&theme=dark`;
}

function talkIsOpen() {
  const stored = localStorage.getItem(TALK_OPEN);
  return stored === null ? matchMedia(TALK_WIDE).matches : stored === '1';
}

function syncTalk(links) {
  const button = document.getElementById('talkbtn');
  button.hidden = !links;
  if (!links) return;
  const open = talkIsOpen();
  const frame = document.getElementById('talkframe');
  if (open && frame.dataset.url !== talkUrl(links)) {
    frame.dataset.url = talkUrl(links);
    frame.src = frame.dataset.url;
  }
  document.getElementById('talk').hidden = !open;
  document.body.classList.toggle('talk-open', open);
  button.classList.toggle('on', open);
}

function sendTalkDraft() {
  const frame = document.getElementById('talkframe');
  if (talk.draft === null || !frame.contentWindow || !frame.dataset.url) return;
  frame.contentWindow.postMessage({ type: 'cc-present.draft', text: talk.draft }, new URL(frame.dataset.url).origin);
  talk.draft = null;
}

function openTalk(question) {
  localStorage.setItem(TALK_OPEN, '1');
  talk.draft = question;
  syncTalk(state.model.talk);
  sendTalkDraft();
}

document.addEventListener('DOMContentLoaded', () => {
  document.getElementById('talkframe').addEventListener('load', sendTalkDraft);
  document.getElementById('talkbtn').addEventListener('click', () => {
    localStorage.setItem(TALK_OPEN, talkIsOpen() ? '0' : '1');
    syncTalk(state.model.talk);
  });
  document.getElementById('talkclose').addEventListener('click', () => {
    localStorage.setItem(TALK_OPEN, '0');
    syncTalk(state.model.talk);
  });
});
