import { SelectField } from "../../components/ui";
import { FILE_TYPES, SORTS, type Filter } from "./entries";

/** Result count and the type and sort selectors. */
export function FilterBar({
  shown,
  total,
  filter,
  onType,
  onSort,
}: {
  shown: number;
  total: number;
  filter: Filter;
  onType: (type: string) => void;
  onSort: (sort: string) => void;
}) {
  return (
    <div className="library-tools">
      <span id="fileCount" role="status">
        {shown}件{shown !== total && ` / ${total}件`}
      </span>
      <div className="list-filters">
        <SelectField
          id="fileType"
          aria-label="ファイルの種類"
          value={filter.type}
          onChange={(e) => onType(e.target.value)}
        >
          <option value="">すべての種類</option>
          {FILE_TYPES.map(([value, label]) => (
            <option key={value} value={value}>
              {label}
            </option>
          ))}
        </SelectField>
        <SelectField
          id="fileSort"
          aria-label="並べ替え"
          value={filter.sort}
          onChange={(e) => onSort(e.target.value)}
        >
          {SORTS.map(([value, label]) => (
            <option key={value} value={value}>
              {label}
            </option>
          ))}
        </SelectField>
      </div>
    </div>
  );
}
