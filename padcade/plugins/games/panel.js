// The Games page's own part of the management page: the way to the list of games.
export default kit => ({
  main: standard => [...standard, kit.el('button', {className: 'link', style: 'justify-self:start', textContent: 'Add or remove games', onclick: () => kit.plugins('game')})],
});
