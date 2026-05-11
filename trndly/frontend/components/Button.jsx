// Button.jsx — trndly primary button
// Variants: primary (forest), secondary (white), pop (mustard), ghost
// Sizes: default, sm
// Exports to window: Button

const buttonStyles = {
  base: {
    display: 'inline-flex', alignItems: 'center', justifyContent: 'center', gap: 8,
    padding: '10px 18px', borderRadius: 9999,
    fontFamily: 'var(--font-sans)', fontSize: 15, fontWeight: 600,
    border: '2px solid #1a1a1a', cursor: 'pointer',
    boxShadow: '2px 2px 0 0 #1a1a1a',
    transition: 'transform 160ms cubic-bezier(0.34,1.4,0.64,1), box-shadow 160ms cubic-bezier(0.2,0,0,1)',
    userSelect: 'none', textDecoration: 'none',
  },
  primary:   { background: '#2d5e3e', color: '#fbf6ee' },
  secondary: { background: '#ffffff', color: '#1a1a1a' },
  pop:       { background: '#e8b840', color: '#1a1a1a' },
  ghost:     { background: 'transparent', color: '#1a1a1a', borderColor: 'transparent', boxShadow: 'none' },
  sm:        { padding: '6px 12px', fontSize: 13 },
};

function Button({ variant = 'primary', size, children, onClick, style, disabled, type }) {
  const [hovered, setHovered] = React.useState(false);
  const [pressed, setPressed] = React.useState(false);

  const variantStyle = buttonStyles[variant] || buttonStyles.primary;
  const sizeStyle = size === 'sm' ? buttonStyles.sm : {};

  let dynamicStyle = {};
  if (variant !== 'ghost') {
    if (pressed)       dynamicStyle = { transform: 'translate(1px,1px)', boxShadow: '1px 1px 0 0 #1a1a1a' };
    else if (hovered)  dynamicStyle = { transform: 'translate(-1px,-1px)', boxShadow: '4px 4px 0 0 #1a1a1a' };
  } else {
    if (hovered) dynamicStyle = { background: '#f5ede0' };
  }

  return (
    <button
      style={{ ...buttonStyles.base, ...variantStyle, ...sizeStyle, ...dynamicStyle, ...style,
               opacity: disabled ? 0.5 : 1, pointerEvents: disabled ? 'none' : 'auto' }}
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => { setHovered(false); setPressed(false); }}
      onMouseDown={() => setPressed(true)}
      onMouseUp={() => setPressed(false)}
      onClick={onClick}
      disabled={disabled}
      type={type}
    >
      {children}
    </button>
  );
}

Object.assign(window, { Button });
