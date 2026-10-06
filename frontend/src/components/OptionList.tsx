import { Markup } from './Markup';

interface Props {
  options: string[];
  /** The learner's pick (draft or saved). */
  selected: number | null;
  /** When set, the key is revealed: correct option green, a wrong pick red. */
  answer?: number | null;
  onSelect?: (index: number) => void;
  name: string;
}

export function OptionList({ options, selected, answer = null, onSelect, name }: Props) {
  const revealed = answer !== null;
  return (
    <div className="options" role="radiogroup">
      {options.map((text, i) => {
        const isAnswer = revealed && i === answer;
        const isWrongPick = revealed && i === selected && i !== answer;
        const cls = ['option', i === selected ? 'is-selected' : '', isAnswer ? 'is-correct' : '', isWrongPick ? 'is-wrong' : '']
          .filter(Boolean).join(' ');
        return (
          <label key={i} className={cls}>
            <input
              type="radio"
              name={name}
              checked={i === selected}
              disabled={!onSelect}
              onChange={() => onSelect?.(i)}
            />
            <span className="option-num">{i + 1}</span>
            <Markup text={text} inline className="option-text" />
            {isAnswer && <span className="option-tag tag-good">✓ Correct answer</span>}
            {isWrongPick && <span className="option-tag tag-bad">✗ Your answer</span>}
          </label>
        );
      })}
    </div>
  );
}
