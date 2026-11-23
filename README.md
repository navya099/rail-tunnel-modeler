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

[예제 프로젝트](examples/crossing.json)처럼 LandXML 파일 하나에서 본선 터널 구간을 선택합니다. 간섭 검토에는 본선 하나 이상과 총 두 개 이상의 모델이 필요합니다. 각 본선 구간에 선형 이름, 시작·종료 측점, 단면 JSON, 선형 표고가 뜻하는 단면 기준(`FL` 또는 `RL`)을 지정합니다. 종단선형이 여러 개면 `profile`을 지정합니다. 단면 파일은 기존 스냅샷 목록 또는 단면 제원 객체를 받습니다. 목록의 `snapshot_index`는 0부터 시작합니다. 상대 파일 경로는 프로젝트 JSON의 폴더를 기준으로 읽습니다.

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

### 모델과 결과

단면은 평면선형에 직교하는 수직면에 배치합니다. 측점 간격 상한은 기본 2m이며, `--max-step`으로 바꿀 수 있습니다. 곡선에서는 회전량에 따라 구간을 추가로 나눕니다. 이격과 측점은 샘플링한 3D 형상에서 산출합니다. JSON 좌표는 미터 단위의 동·북·표고입니다. GLB는 보고서의 `model_origin_enz_m`을 뺀 로컬 좌표를 사용하며, 축은 동·표고·남 순서입니다.

현재 간섭 검토는 프로젝트에 지정한 터널과 부속 갱의 외곽을 대상으로 합니다. NATM은 기존 내공 계산의 평탄한 바닥 경계를 사용합니다.

### 단면 계산 코드

`src/rail_tunnel_modeler/section_core`는 기존 `tunnel_designer`의 `constants.py`, `models.py`, `geometry.py`, `snapshot.py`를 가져온 것입니다. 스냅샷 모듈의 import 경로를 새 패키지에 맞췄습니다. NATM 외곽을 이어 붙이고 입력 단면을 검증하는 코드는 `sections.py`에 있습니다. [원본 커밋](https://github.com/navya099/test/tree/907165108114454e4cb56026a554e37f4253316a/tunnel_designer)
