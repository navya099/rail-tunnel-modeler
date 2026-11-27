# Rail Tunnel Modeler

개발 코드네임은 **지맥(Jimaek)**입니다. 철도 노선 기획·개념설계 단계에서 LOD 200 터널 모델을 빠르게 생성하고, 상·하부의 도로터널·철도터널과 지장물, 터널 내부 철도시설물의 간섭을 검토하는 로컬 데스크톱 프로그램입니다. 터널 형상은 내공과 외곽 경계까지 모델링합니다. 구조 검토와 록볼트·라이닝 상세는 범위에서 제외합니다.

## 설계 범위

| 항목 | 요구사항 |
| --- | --- |
| 선형과 지형 | LandXML 평면·종단선형을 읽고, DEM 또는 LandXML Surface(TIN)에서 지형을 구성합니다. 좌표계, 단위, 표고 기준을 확인하고 표기 측점과 선형상의 누적거리를 관리합니다. |
| 본선 터널 | 구간별 단면을 선형에 배치해 내공과 외곽의 3D 형상을 생성합니다. 단일 NATM 아치형, 2-Arch 분기부, 3-Arch 정거장, 개착 Box형과 단면 전이구간을 지원합니다. |
| 부속 갱과 접속부 | 중간 경사갱, 기재갱, 대피수직구, 환기수직구, 분기터널, 상·하부 교차터널을 모델링합니다. 접속부의 의도된 겹침은 지정 범위 안에서 허용합니다. |
| 진·출입구 | 갱문과 개착터널의 개념 형상을 생성하고, 지정한 절토사면 조건으로 지형과 만나는 경계를 계산합니다. |
| 차량과 건축한계 | 궤도를 기준으로 차량 형상과 건축한계를 각각 배치합니다. 건축한계의 내공 침범과 철도시설물의 건축한계 침범을 검토합니다. |
| 간섭 결과 | 간섭 객체, 위치, 관련 선형의 측점, 최소 3D 이격을 표시합니다. 교차터널에서는 양쪽 터널의 측점과 상하 관계를 함께 표시합니다. |

## 데스크톱 구성

Python 계산 엔진과 3D 뷰어를 분리합니다. 지형은 타일 단위로 로드하고, 터널 형상은 측점 구간별로 생성해 로컬에 캐시합니다. 단면이나 선형이 바뀌면 영향받는 구간을 다시 계산합니다. 공간 인덱스로 간섭 후보를 추린 뒤 형상 간 겹침과 이격을 계산합니다. 원본 좌표 참조는 보존하고 뷰어에서는 가까운 기준점을 원점으로 사용합니다.

기존 `tunnel_designer`의 스냅샷 JSON과 GUI에 독립적인 단면 계산 코드를 재사용합니다. 연동 시 내공·외곽 윤곽과 FL·RL 기준, 단위를 전달합니다. 단일 NATM 외곽의 원호 연결을 보완하고, 2·3-Arch의 전체 외곽 경계를 정의합니다. 개착 Box의 내공이 기둥으로 나뉘면 모든 영역을 보존합니다. [기존 단면 도구](https://github.com/navya099/test/tree/main/tunnel_designer)

## 첫 구현 범위

1. 기존 스냅샷에서 단일 NATM과 개착 Box 단면을 복원합니다.
2. LandXML 선형의 지정 구간에 두 단면 형식을 배치해 내공과 외곽을 생성합니다.
3. 상·하부 교차터널의 간섭 위치와 최소 이격, 양쪽 터널의 측점을 표시합니다.

## 첫 구현 실행

Python 3.11 이상에서 가상환경을 활성화한 뒤 설치합니다. 아래 예제는 가상의 직교 선형 두 개에 NATM과 개착 Box를 배치합니다.

```sh
python -m pip install .
mkdir outputs
rail-tunnel-modeler examples/crossing.json --report outputs/crossing-report.json --glb outputs/crossing.glb
```

예제에서 Box 선형은 NATM 선형보다 높고, 외곽 사이의 최소 이격은 약 **2.462m**입니다. 명령은 결과를 터미널에 출력합니다. `--report`와 `--glb`를 주면 위치와 측점이 담긴 JSON 및 내공·외곽·검토 위치가 담긴 GLB도 저장합니다. 교차 시에는 대표 접촉 위치와 이격 0m를 반환합니다. 한 터널이 다른 터널에 완전히 포함되면 포함된 터널 안의 대표 위치를 반환합니다.

Git은 저장소 루트의 `outputs/`를 무시합니다. 생성한 보고서 JSON과 GLB는 이 폴더에 저장하고, `examples/`의 입력 JSON은 Git으로 관리합니다.

설치 옵션 `viewer`를 사용하면 로컬 3D 창에서 모델과 결과를 확인할 수 있습니다.

```sh
python -m pip install ".[viewer]"
rail-tunnel-modeler examples/crossing.json --view
```

### 프로젝트 입력

[예제 프로젝트](examples/crossing.json)처럼 LandXML 파일 하나에서 본선 터널 구간을 선택합니다. 터널 간 검토에는 총 두 개 이상의 터널·부속 갱 모델이 필요하며, 내공과 철도시설물 검토는 본선 하나로도 실행할 수 있습니다. 각 본선 구간에 선형 이름, 시작·종료 측점, 단면 JSON, 선형 표고가 뜻하는 단면 기준(`FL` 또는 `RL`)을 지정합니다. 종단선형이 여러 개면 `profile`을 지정합니다. 단면 파일은 기존 스냅샷 목록 또는 단면 제원 객체를 받습니다. 목록의 `snapshot_index`는 0부터 시작합니다. 상대 파일 경로는 프로젝트 JSON의 폴더를 기준으로 읽습니다.

현재 LandXML 입력은 미터 단위의 직선·원곡선·클로소이드 및 PVI·대칭 포물선 종단곡선을 지원합니다. 구간 입력에는 연속 측점을 사용합니다. `StaEquation`이 있으면 JSON에 연속 측점과 보정된 표기 측점을 함께 출력합니다. 두 구간의 좌표계와 표고 기준은 입력 LandXML에서 일치해야 합니다.

### 다중 아치와 단면 전이

2-Arch와 3-Arch는 기존 스냅샷의 아치 제원과 중벽·아치 간격을 읽습니다. 아치별 내공은 각각 모델링하고, 겹치는 내공은 입력 오류로 표시합니다. 전체 외곽은 기본적으로 각 아치 외곽을 감싸는 볼록껍질로 생성합니다. 이 경계는 아치 사이의 공간도 포함하므로 간섭을 보수적으로 판정합니다. 실제 계획 외곽이 있으면 `outer_boundary`에 단면 좌표계 기준 `[x, y]` 꼭짓점을 순서대로 지정할 수 있습니다.

전이구간은 터널의 `section` 대신 `sections` 목록에 `station`, `section` 및 필요한 경우 `snapshot_index`와 `outer_boundary`를 넣어 지정합니다. 측점은 모델링 구간 안에서 오름차순이어야 합니다. 지정한 측점 사이에서는 내공과 외곽을 선형 보간하고, 첫 단면과 마지막 단면은 모델링 구간의 양 끝까지 유지합니다. 전이 양쪽의 내공 구획 수와 경계별 구멍 수가 같아야 합니다. 예를 들어 2-Arch의 폭 변경은 지원하며, 내공이 둘로 갈라지는 접속 형상은 분기 모델링에서 다룹니다.

```sh
rail-tunnel-modeler examples/section-transition.json --glb outputs/transition.glb --report outputs/transition-report.json
```

[전이 예제](examples/section-transition.json)는 2-Arch 단면을 1031m부터 1073m까지 확장하고 상부 3-Arch 정거장 구간과의 이격을 확인합니다.

### 부속 갱과 접속부

프로젝트 JSON의 `auxiliaries` 목록에서 본선에 접속할 부속 갱을 정의합니다. 이름은 프로젝트에서 고유해야 합니다.

| 형식 | 형상 입력 |
| --- | --- |
| 경사갱 `inclined_gallery`, 기재갱 `equipment_gallery`, 분기터널 `branch` | 접속점에서 `end_enz_m`의 동·북·표고 좌표까지 직선으로 배치합니다. 기존 터널과 같은 `section` 또는 `sections` 및 `datum`을 사용합니다. 부속 갱 자체의 측점은 접속점부터 수평거리로 계산합니다. |
| 대피수직구 `escape_shaft`, 환기수직구 `ventilation_shaft` | 접속점과 같은 평면 좌표에서 `end_elevation_m`까지 원형 단면을 연장합니다. `inner_diameter_m`과 `outer_diameter_m`로 직경을 지정합니다. 수직구의 측점은 접속점부터의 수직거리입니다. |

`connection.tunnel`은 본선 구간 이름이고 `connection.station`은 해당 본선의 연속 측점입니다. `connection.offset_m`은 측점 위치에서 `[오른쪽 오프셋, 표고 오프셋]`이며 기본값은 `[0, 0]`입니다. `connection.allow_overlap_box_m`에는 오프셋을 반영한 접속점을 중심으로 하는 허용 상자의 동·북·표고 방향 **전체 길이**를 입력합니다.

지정된 본선과 부속 갱 사이에서만 상자 안의 겹침을 허용합니다. 허용 범위 밖의 겹침은 간섭으로 보고하며, 다른 터널이나 부속 갱과의 간섭에는 허용 상자를 적용하지 않습니다. `allowed_connection`의 이격은 상자 밖에 남은 부속 갱 외곽을 기준으로 계산합니다. 상자가 부속 갱 전체를 포함하면 이격은 `null`입니다. 지정한 쌍의 외곽이 만나지 않으면 `unconnected`로 표시합니다. GLB와 뷰어에는 각 객체의 내공과 외곽이 독립된 형상으로 남습니다.

```sh
rail-tunnel-modeler examples/auxiliaries.json --glb outputs/auxiliaries.glb --report outputs/auxiliaries-report.json
```

[부속 갱 예제](examples/auxiliaries.json)는 본선 하나에 다섯 가지 부속 갱을 배치합니다. 보고서의 `checks`에는 모든 객체 쌍의 결과가 들어갑니다. 보고서 상단과 뷰어는 간섭, 미접속, 최소 이격 순으로 우선 표시할 쌍을 선택합니다.

### 차량·건축한계와 철도시설물

`clearances`에 본선 `tunnel`, 궤도 `name`, `gauge`를 지정합니다. `start_station`과 `end_station`을 생략하면 본선 모델링 구간 전체를 사용합니다. `gauge.outline`과 선택 항목인 `vehicle.outline`에는 궤도 중심과 레일면(`RL`) 기준의 `[오른쪽 거리, 높이]`를 미터 단위로 입력합니다. `track_offset_m`과 `vertical_track_offset_m`은 각각 오른쪽과 위쪽이 양수이며 기본값은 0입니다. 단면이 바뀌면 단면별 기준에 따른 레일 높이도 보간합니다. 각 윤곽의 `cant_deg`는 레일면 중심을 기준으로 오른쪽을 들어 올리는 방향이 양수입니다. 차량은 그 단면을 지정 구간에 배치한 개념 이동 포락체로 모델링합니다.

`facilities`에는 같은 본선의 `tunnel`, 고유 `name`, 중심 `station`, 중심 오프셋 `offset_m`, `size_m`을 지정합니다. 시설물 중심 오프셋은 `[오른쪽 거리, 선형 표고에서의 높이]`입니다. 크기는 `[선형 방향 길이, 폭, 높이]`이며 시설물은 측점의 평면 방향을 따르는 박스입니다.

보고서의 `rail_checks`에는 건축한계가 전체 내공 영역에서 벗어난 부피와 대표 위치, 시설물과 건축한계·차량 사이의 최소 이격과 대표 측점이 들어갑니다. 내공 경계에 닿는 건축한계는 허용하며 돌출 부피 판정 허용오차는 0.000001m³입니다. 시설물이 한계 또는 차량 포락체에 닿으면 간섭으로 표시합니다. 터널 간 검토 결과는 보고서 상단에 따로 표시합니다.

```sh
rail-tunnel-modeler examples/rail-clearance.json --report outputs/rail-clearance-report.json --glb outputs/rail-clearance.glb
```

[철도시설물 예제](examples/rail-clearance.json)는 표지판의 건축한계 침범, 차량의 28mm 이격, 다른 궤도의 건축한계 내공 돌출을 보여줍니다. `--view`를 추가하면 각 형상과 간섭 표시를 확인할 수 있습니다.

### DEM과 LandXML 지형

프로젝트 JSON에 `terrain`을 추가하면 본선과 부속 갱 중심선 주변의 지형을 함께 표시합니다. `corridor_m`은 중심선에서 양쪽으로 적용할 수평 거리이며 기본값은 50m입니다. 보고서의 `terrain`에는 지형 이름, 좌표계, 타일·삼각형 수와 동·북·표고 범위가 들어갑니다. 본선 하나와 지형만 있는 프로젝트도 실행할 수 있습니다.

- **LandXML Surface**는 `type`을 `landxml_surface`로 지정하고 `surface`에 TIN 이름을 입력합니다. `path`를 생략하면 선형 LandXML 안의 Surface를 읽습니다. 삼각형은 회랑과 교차하는 원래 면을 사용하며, 선형과 TIN은 같은 수평 좌표계를 사용해야 합니다.
- **DEM**은 `python -m pip install ".[terrain]"`으로 지원을 설치하고, `type`을 `dem`으로 지정합니다. `path`의 래스터 첫 번째 밴드는 미터 단위 표고이며 원본 CRS가 필요합니다. LandXML에 EPSG 코드가 없으면 `project_crs`에 미터 단위 투영 좌표계를 입력합니다. DEM은 이 좌표계로 변환하고 회랑에 닿는 창 단위로 읽습니다. `dem_stride`는 원본 픽셀의 샘플 간격이며 기본값은 1입니다. 타일 경계에서는 샘플을 공유하고, NoData가 포함된 셀은 비워 둡니다.

두 형식 모두 `vertical_offset_m`으로 지형 표고에 보정값을 더할 수 있습니다. 표고 기준은 입력 자료에서 확인합니다. 다음은 GeoTIFF를 사용할 때의 설정 예입니다.

```json
{
  "terrain": {
    "type": "dem",
    "path": "ground.tif",
    "project_crs": "EPSG:5179",
    "corridor_m": 75,
    "dem_stride": 2
  }
}
```

```sh
rail-tunnel-modeler examples/terrain-tin.json --glb outputs/terrain-tin.glb --report outputs/terrain-tin-report.json --view
```

[TIN 예제](examples/terrain-tin.json)는 터널과 원지형을 표시합니다. 로컬 뷰어에서 `T`를 누르면 지형 표시를 켜고 끌 수 있습니다. GLB는 지형을 별도 노드로 포함합니다. `--view`에는 `viewer` 설치 옵션이 필요합니다.

### 갱문과 절토사면

`portals`에 고유 `name`, 본선 모델 이름 `tunnel`, 배치할 구간 끝 `end`(`start` 또는 `end`)를 지정합니다. 갱문은 해당 끝 단면의 외곽을 감싸는 직사각형 개념 벽체이며, 모든 내공 구획을 개구부로 유지합니다. `wall_margin_m`은 외곽에 더할 여유 폭으로 기본값은 0.5m입니다. `wall_thickness_m`은 터널 바깥 방향으로 연장할 두께이며 기본값은 0.8m입니다. 같은 설정을 NATM, 다중 아치, 개착 Box 구간에 적용할 수 있습니다.

`approach_cut`을 지정하면 본선 모델의 끝에서 바깥 방향으로 LandXML 선형을 따라 절토사면을 생성합니다. 접근부 전 길이는 입력 평면·종단선형 범위 안에 있어야 합니다.

| 필드 | 의미 |
| --- | --- |
| `length_m` | 본선 모델 끝에서 바깥쪽으로 모델링할 접근부 길이 |
| `bottom_width_m` | 좌우 사면 시작점 사이의 전체 폭 |
| `toe_offset_m` | 선형 표고에서 사면 시작점까지의 높이 차이. 기본값은 0m |
| `slope_hv.left`, `slope_hv.right` | 좌우 사면의 수평거리/수직높이 비율. 좌우는 측점 증가 방향 기준 |
| `max_run_m` | 각 사면 시작점부터 지형 접점을 찾을 최대 수평거리 |
| `benches` | 선택 항목. 각 소단의 시작 높이 `rise_m`과 수평 폭 `width_m`을 낮은 순서로 입력 |

각 접근부 측점에서 사면과 원지형 삼각형의 첫 교점을 계산합니다. DEM은 표시용 `dem_stride` 적용 전 해상도로 절토 접점을 계산합니다. 보고서에는 `daylight`와 접점 좌표, `no_cut`과 사면 시작점, 또는 접점을 얻을 수 없는 상태가 들어갑니다. `no_terrain`은 사면 시작점에 지형이 없는 경우, `terrain_gap`은 접점 이전에 지형이 끊긴 경우, `no_daylight`는 검색 범위 안에서 지형에 닿지 않은 경우입니다. 지형이 끊긴 측점을 가로질러 사면을 연결하지 않습니다. 원지형, 갱문, 사면은 별도 형상으로 내보냅니다.

```sh
rail-tunnel-modeler examples/portals.json --glb outputs/portals.glb --report outputs/portals-report.json --view
```

[갱문 예제](examples/portals.json)는 1030m 진입부와 1070m 출입부에 갱문을 배치합니다. 진입부의 두 사면에는 소단이 포함됩니다.

### 개착 Box 굴착구간

본선 구간의 `cut_and_cover`에 굴착 조건을 입력하면 개착 Box 외곽을 따라 계획 굴착 바닥면과 좌우 사면을 생성합니다. 해당 구간의 모든 단면은 개착 Box여야 하고 프로젝트에 DEM 또는 LandXML 지형이 필요합니다. `working_space_m`은 Box 외곽 좌우에 더할 `[왼쪽, 오른쪽]` 여유 폭이며 기본값은 각각 1m입니다. `overdig_m`은 Box 외곽 바닥에서 추가로 내릴 깊이이며 기본값은 0m입니다. 사면은 갱문 접근부와 같은 `slope_hv`, `max_run_m`, `benches`를 사용합니다. 단면 전이구간에서는 변하는 박스 폭과 높이를 적용합니다.

보고서의 `cut_and_cover.sections`에는 측점별 굴착 바닥 폭·표고, Box 외곽 상단 표고, 중심선 원지형 표고와 토피가 들어갑니다. 토피는 중심선 원지형 표고에서 Box 외곽 상단 표고를 뺀 값입니다. 지형 데이터가 없으면 토피는 `null`이며, 음수 토피는 Box 상단이 그 위치의 원지형보다 높다는 뜻입니다. 좌우 사면 접점과 지형 공백은 `cut_and_cover.daylight`에서 확인할 수 있습니다. GLB와 뷰어는 Box, 원지형, 굴착 바닥면과 사면을 각각 표시합니다.

```sh
rail-tunnel-modeler examples/cut-and-cover.json --glb outputs/cut-and-cover.glb --report outputs/cut-and-cover-report.json --view
```

[개착 굴착 예제](examples/cut-and-cover.json)는 1030m부터 1070m까지 좌우 작업 여유와 소단을 포함한 굴착구간을 생성합니다.

### 기존 터널과 지장물

`obstacles`에 기존 도로터널(`road_tunnel`), 철도터널(`rail_tunnel`), 관로 등 지장물(`utility`), 구조물(`structure`)을 등록합니다. 각 항목에는 고유 `name`, `kind` 및 다음 형상 중 하나가 필요합니다. 좌표와 표고는 선형과 같은 기준의 미터 단위를 사용합니다.

| 형상 | 입력 |
| --- | --- |
| `box` | 중심 `center_enz_m`의 `[동, 북, 표고]`, `size_m`의 `[길이, 폭, 높이]`, 선택 항목 `bearing_deg`. 방향은 북쪽에서 시계 방향으로 재며 기본값은 북쪽입니다. |
| `pipe` | `points_enz_m`에 관로 중심선의 `[동, 북, 표고]` 좌표를 순서대로 입력하고 `diameter_m`로 외경을 지정합니다. 각 선분을 원형 단면과 둥근 끝을 가진 형상으로 배치합니다. |
| `glb` | `path`, `origin_enz_m` 및 필요하면 `nodes`를 지정합니다. GLB의 X·Y·Z는 동·표고·남 방향입니다. 원점은 프로젝트 좌표계의 `[동, 북, 표고]`입니다. 선택한 형상은 닫힌 솔리드여야 합니다. |

지맥에서 내보낸 GLB를 다시 쓸 때는 원래 보고서의 `model_origin_enz_m`을 복사하고, 예를 들어 `"nodes": ["Box/outer"]`처럼 검토할 외곽 노드를 지정합니다. `nodes`를 생략하면 GLB의 모든 형상 노드를 불러옵니다.

프로젝트 전체의 기준 이격은 `obstacle_clearance_m`으로 정하며 기본값은 0m입니다. 지장물별 `required_clearance_m`이 있으면 그 값을 사용합니다. `obstacle_checks`는 지장물마다 본선과 부속 갱의 외곽을 비교해 `contact_or_overlap` 또는 `containment`, `below_clearance`, `clear`를 보고합니다. 결과에는 최소 3D 이격, 양쪽 형상의 좌표와 설계 터널 측점이 들어갑니다. 뷰어는 간섭 또는 기준 미달 위치를 표시하고, 기준 미달인 경우 최단점을 선으로 연결합니다. 본선 하나만으로도 이 검토를 실행할 수 있습니다.

```sh
rail-tunnel-modeler examples/obstacles.json --glb outputs/obstacles.glb --report outputs/obstacles-report.json --view
```

[지장물 예제](examples/obstacles.json)는 상부 도로터널의 이격 0.862m와 기준 1.0m, 기존 철도터널의 겹침, 관로의 이격 확보를 함께 보여줍니다.

### 모델과 결과

단면은 평면선형에 직교하는 수직면에 배치합니다. 측점 간격 상한은 기본 2m이며, `--max-step`으로 바꿀 수 있습니다. 곡선에서는 회전량에 따라 구간을 추가로 나눕니다. 이격과 측점은 샘플링한 3D 형상에서 산출합니다. JSON 좌표는 미터 단위의 동·북·표고입니다. GLB는 보고서의 `model_origin_enz_m`을 뺀 로컬 좌표를 사용하며, 축은 동·표고·남 순서입니다.

터널 간 간섭 검토에는 터널과 부속 갱의 외곽을 사용합니다. NATM은 기존 내공 계산의 평탄한 바닥 경계를 사용합니다.

### 단면 계산 코드

`src/rail_tunnel_modeler/section_core`는 기존 `tunnel_designer`의 `constants.py`, `models.py`, `geometry.py`, `snapshot.py`를 가져온 것입니다. 스냅샷 모듈의 import 경로를 새 패키지에 맞췄습니다. NATM 외곽을 이어 붙이고 입력 단면을 검증하는 코드는 `sections.py`에 있습니다. [원본 커밋](https://github.com/navya099/test/tree/907165108114454e4cb56026a554e37f4253316a/tunnel_designer)
